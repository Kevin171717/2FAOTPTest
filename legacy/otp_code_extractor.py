import os
import re
from imap_tools import MailBox
from zoneinfo import ZoneInfo
from datetime import timedelta
import pyotp
from sentence_transformers import SentenceTransformer, util
from urlextract import URLExtract
from bs4 import BeautifulSoup
import math
import torch
import ssl
import time
from urllib.parse import urlparse, parse_qs
import cv2
import numpy as np

class OTPCodeExtractor:

    def __init__(self, embedding_model=None, notification_margin: float | None = None):
        self.EMAIL = os.getenv("GMAIL_USER") if os.getenv("GMAIL_USER") else None
        self.PASSWORD = os.getenv("GMAIL_PASS") if os.getenv("GMAIL_PASS") else None
        self.IMAP_SERVER = "imap.gmail.com"
        self.IMAP_PORT = 993
        self.TOTP_SECRET = os.getenv("TOTP_SECRET").replace(" ", "") if os.getenv("TOTP_SECRET") else None
        # self.otp_pattern = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]{4,8}(?![A-Za-z0-9]))(?=[A-Za-z0-9]{0,7}\d)[A-Za-z0-9]{4,8}")
        self.otp_pattern = re.compile(r"(?<![A-Za-z0-9])(?:\d{3}\s\d{3}|(?=[A-Za-z0-9]{4,8}(?![A-Za-z0-9]))(?=[A-Za-z0-9]{0,7}\d)[A-Za-z0-9]{4,8})")
        self.url_pattern = re.compile(r"https?://[^\s]+")
        if embedding_model is None:
            self.bge_model = SentenceTransformer("BAAI/bge-large-zh-v1.5", model_kwargs={"use_safetensors": False})
        else:
            self.bge_model = embedding_model
        self.otp_templates = [
            "此訊息包含一次性驗證碼",
            "請輸入此驗證碼完成登入",
            "這是一則兩步驟驗證碼通知",
            "此安全碼用於帳號驗證",
            "one time password for login verification",
            "security code for account verification",
            "two factor authentication code",
            "here is your verification code",
            "這是您的代碼，請勿與他人分享。",
            "此代碼將於 15 分鐘到期。您未要求此代碼？",
            "您的驗證碼為",
            "是您的代碼",
            "登入代碼",
            "login code",
        ]
        self.url_templates = [
            "點擊連結驗證電子郵件",
            "確認電子郵件地址",
            "啟用帳號",
            "完成註冊",
            "確認並登入",
            "立即驗證",
            "請點選下方連結完成驗證",
            "verify your email address",
            "confirm your account",
            "activate your registration",
            "Confirm device"
        ]
        self.notification_templates = [
            "This is a security notification about recent account activity.",
            "A new device or browser signed in to your account.",
            "Two-factor authentication was enabled on your account.",
            "Two-factor authentication was disabled on your account.",
            "Your authentication method or phone number was changed.",
            "Review this account activity if it was not you.",
            "這是一封帳戶安全活動通知。",
            "有新的裝置或瀏覽器登入您的帳戶。",
            "您的雙重驗證已啟用。",
            "您的雙重驗證已停用。",
            "您的驗證方式或電話號碼已變更。",
            "如果不是您本人操作，請檢查帳戶活動。",
        ]
        self.noise_patterns = [
            r'\d{4}[-/]\d{2}[-/]\d{2}',
            r'(?i)(?<![A-Za-z])(NT\$?|\$)\s?[\d,]+(\.\d{2})?',
            r'[0-2]?\d:[0-5]\d',
            r'(?<!\d)\d{10,12}(?!\d)'
        ]
        self.otp_template_embeddings = self.bge_model.encode(self.otp_templates, normalize_embeddings=True, convert_to_tensor=True)
        self.url_template_embeddings = self.bge_model.encode(self.url_templates, normalize_embeddings=True, convert_to_tensor=True)
        self.notification_margin = notification_margin
        self.notification_template_embeddings = None
        if notification_margin is not None:
            self.notification_template_embeddings = self.bge_model.encode(
                self.notification_templates,
                normalize_embeddings=True,
                convert_to_tensor=True,
            )
        # self.window = 50
        # self.otp_threshold = 0.5
        self.anchor_threshold = 0.50
        self.url_threshold = 0.5
        self.max_otp_anchor_distance = 1500
        self.url_extractor = URLExtract()

    def fetch_one_mail_nearest(self, target_time, max_minutes=5, subject_filter=None, idle_timeout=90) -> dict | None:
        """
        Polls Gmail IMAP for the unread message closest to target_time that contains
        an OTP code or verification URL.

        IMAP config (set once via .env, shared for the whole session):
            GMAIL_USER — Gmail address used as the 2FA receiving inbox.
            GMAIL_PASS — Gmail App Password (not the account password); requires
                         "Less secure app access" or an App Password under
                         Google Account > Security > 2-Step Verification.
            IMAP host/port are hardcoded to imap.gmail.com:993 (Gmail only).

        Args:
            target_time: timezone-aware datetime (Asia/Taipei) of when the OTP was
                         requested. Only messages sent on or after (target_time - 1 day)
                         are searched; the nearest one wins.
            max_minutes:  ignored in current implementation (kept for API compatibility).
            subject_filter: optional IMAP SUBJECT filter string.
            idle_timeout: seconds to wait for a matching email before returning None
                          and triggering a resend in the orchestrator.

        Returns:
            dict with keys {"text", "msg"} for the nearest matching message, or
            None if no qualifying message arrives within idle_timeout seconds.
        """
        print(f"Fetching unseen email nearest to target time (account: {self.EMAIL})")

        footer_pattern = (
            r"(?:\r?\n|\\r\\n)+From\s+.*?via\s+Android.*$"
        )

        search_date = (target_time - timedelta(days=1)).strftime("%d-%b-%Y")

        for attempt in range(1, 4):
            try :
                with MailBox(self.IMAP_SERVER).login(self.EMAIL, self.PASSWORD, "INBOX") as mailbox:

                    if subject_filter:
                        search_criteria = f'UNSEEN SINCE {search_date} SUBJECT "{subject_filter}"'
                    else:
                        search_criteria = f'UNSEEN SINCE {search_date}'

                    deadline = time.monotonic() + idle_timeout

                    while True:

                        messages = list(mailbox.fetch(search_criteria))

                        if messages:
                            nearest_msg = None
                            nearest_clean_text = None
                            min_diff = None

                            for msg in messages:
                                if msg.html:
                                    soup = BeautifulSoup(msg.html, 'html.parser')
                                    for a in soup.find_all('a', href=True):
                                        link_text = a.get_text(" ", strip=True)
                                        href = a["href"].strip()
                                        replacement = f" {link_text} {href} " if link_text else f" {href} "
                                        a.replace_with(replacement)
                                    raw_text = soup.get_text()
                                else:
                                    raw_text = msg.text or ""
                                raw_text = re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]+', '', raw_text)
                                raw_text = raw_text.replace('\xa0', '')
                                # print(repr(raw_text))
                                clean_text = re.sub(footer_pattern, "", raw_text.rstrip(), flags=re.IGNORECASE | re.DOTALL).rstrip()

                                if not self.otp_pattern.search(clean_text) and not self.url_pattern.search(clean_text):
                                    continue

                                msg_time = msg.date.astimezone(ZoneInfo("Asia/Taipei"))
                                diff = abs((msg_time - target_time).total_seconds())
                                print(f"Find an Email, diff {diff/60:.1f} minutes")

                                if min_diff is None or diff < min_diff:
                                    min_diff = diff
                                    nearest_msg = msg
                                    nearest_clean_text = clean_text
                                    nearest_clean_text = nearest_clean_text.replace('\r\n', '\n').replace('\r', '\n')
                                    nearest_clean_text = '\n'.join(line.strip() for line in nearest_clean_text.split('\n'))
                                    nearest_clean_text = re.sub(r'\n+', '\n', nearest_clean_text).strip()

                            # 判斷是否找到符合條件的信件
                            if nearest_msg is not None:
                                if min_diff <= max_minutes * 60:
                                    # 標記已讀並整理回傳資料
                                    mailbox.flag(nearest_msg.uid, "SEEN", True)

                                    final_text = nearest_msg.text or ""
                                    phone_match = re.search(r"From (\+?\d{8,15}|0\d{7,14})\b", final_text)
                                    phone_number = phone_match.group(1) if phone_match else None

                                    message_data = {
                                        "text": nearest_clean_text,
                                        "to": nearest_msg.to[0] if nearest_msg.to else None,
                                        "from": nearest_msg.from_,
                                        "date": nearest_msg.date.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M"),
                                        "phone_number": phone_number,
                                        "via": "Android" if re.search(r"\nvia\s+Android\s*$", final_text, re.IGNORECASE) else "Email"
                                    }
                                    print("Found nearest email within time limit.")
                                    return message_data
                                else:
                                    print(f"Nearest email is too far away: {min_diff/60:.1f} minutes, skip.")
                            else:
                                print("No suitable email found in this fetch.")

                        # --- 如果沒搜到或沒符合的，進入 IDLE ---
                        # IDLE 只會通知「監聽開始後」才到的信，落在 fetch 與 idle 之間空窗的信
                        # 不會有通知。所以超時後不直接放棄，而是繞回去再 fetch 一次（在同一個
                        # idle_timeout 總預算內），只有總時間到了仍查無信才回傳 None。
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            print("IDLE timeout, no new messages.")
                            return None
                        print(f"Waiting for IDLE notification ({remaining:.0f}s left)...")
                        mailbox.idle.wait(timeout=min(remaining, idle_timeout))


                        # if not messages:
                        #     print("No unseen email found.")
                        #     return None

                        # nearest_msg = None
                        # nearest_clean_text = None
                        # min_diff = None

                        # for msg in messages:
                        #     raw_text = (msg.text) or (msg.html and BeautifulSoup(msg.html, 'html.parser').get_text()) or ""
                        #     clean_text = re.sub(footer_pattern, "", raw_text.rstrip(), flags=re.IGNORECASE).rstrip()
                        #     if not self.otp_pattern.search(clean_text) and not self.url_pattern.search(clean_text):
                        #         continue

                        #     msg_time = msg.date.astimezone(ZoneInfo("Asia/Taipei"))
                        #     diff = abs((msg_time - target_time).total_seconds())
                        #     print(f"Find an Email, diff {diff/60:.1f} minutes")
                        #     # print(f"Email at {msg_time}, diff {diff/60:.1f} minutes")

                        #     if min_diff is None or diff < min_diff:
                        #         min_diff = diff
                        #         nearest_msg = msg
                        #         nearest_clean_text = clean_text

                        # if nearest_msg is None:
                        #     print("No suitable email found.")
                        #     return None

                        # if min_diff > max_minutes * 60:
                        #     print(f"Nearest email is too far away: {min_diff/60:.1f} minutes, skip.")
                        #     return None

                        # mailbox.flag(nearest_msg.uid, "SEEN", True)

                        # final_text = nearest_msg.text or ""
                        # # print("\n===== RAW TEXT (best) =====")
                        # # print(final_text)
                        # # print("===== END =====")
                        # # print("\n===== CLEAN TEXT (best) =====")
                        # # print(nearest_clean_text)
                        # # print("===== END =====\n")
                        # phone_match = re.search(r"From (\+?\d{8,15}|0\d{7,14})\b", final_text)
                        # phone_number = phone_match.group(1) if phone_match else None

                        # message_data = {
                        #     "text": nearest_clean_text,
                        #     "to": nearest_msg.to[0] if nearest_msg.to else None,
                        #     "from": nearest_msg.from_,
                        #     "date": nearest_msg.date.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M"),
                        #     "phone_number": phone_number,
                        #     "via": "Android" if re.search(r"\nvia\s+Android\s*$", final_text, re.IGNORECASE) else "Email"
                        # }

                        # print("Found nearest email within time limit.")
                        # # print(f"Message Data:\n {message_data.get('text')}")
                        # return message_data
            except ssl.SSLEOFError as e:
                last_err = e
                print(f"[IMAP] SSL EOF on attempt {attempt}/3")
                time.sleep(min(2 * attempt, 5))

            except Exception:
                raise

        raise last_err


    def extract_otp_code(self, message_data, source, credential_preference) -> tuple[list[dict], str | None]:

        def split_segments(raw_text: str) -> list[tuple[str, int, int]]:
            """
            回傳 [(segment_text, start_pos, end_pos), ...]
            """
            segments = []
            for m in re.finditer(r'[^。\n\r！？!?,，；;]+', raw_text):
                seg = m.group(0).strip()
                if seg:
                    segments.append((seg, m.start(), m.end()))
            return segments

        def semantic_score_from_templates(
            context: str,
            template_embeddings,
            templates: list[str]
        ) -> tuple[float, str]:
            query = "為這個句子生成表示以用於檢索相關文章: " + context
            with torch.no_grad():
                emb = self.bge_model.encode(
                    query,
                    normalize_embeddings=True,
                    convert_to_tensor=True
                )
                scores = util.cos_sim(emb, template_embeddings)[0]
                best_idx_tensor = scores.argmax()
                best_score_tensor = scores[best_idx_tensor]

                final_score = best_score_tensor.item()
                final_idx = best_idx_tensor.item()

            return float(final_score), templates[final_idx]

        def distance_to_anchor(candidate_start: int, candidate_end: int, anchor_start: int, anchor_end: int) -> int:
            """
            候選與 anchor 句的距離：
            - 若候選落在 anchor 句內，距離 = 0
            - 否則取最近邊界距離
            """
            if candidate_end < anchor_start:
                return anchor_start - candidate_end
            elif candidate_start > anchor_end:
                return candidate_start - anchor_end
            else:
                return 0

        def precompute_scored_segments(
            segments: list[tuple[str, int, int]],
            template_embeddings,
            templates: list[str],
        ) -> list[tuple[str, int, int, float, str]]:
            """
            回傳:
            [(seg_text, seg_start, seg_end, score, matched_template), ...]
            """
            scored_segments = []
            for seg_text, seg_start, seg_end in segments:
                score, matched_template = semantic_score_from_templates(
                    seg_text,
                    template_embeddings,
                    templates
                )
                scored_segments.append((seg_text, seg_start, seg_end, score, matched_template))
            return scored_segments

        def get_top_scored_segments(
            scored_segments: list[tuple[str, int, int, float, str]],
            precision: int = 4,
        ) -> tuple[list[tuple[str, int, int, float, str]], float]:
            """
            回傳:
            (top_segments, best_score)

            規則：
            - 找出最高分 segment
            - 若有多個 segment round 後同分，全部保留
            """
            if not scored_segments:
                return [], 0.0

            best_score = max(x[3] for x in scored_segments)
            rounded_best = round(best_score, precision)

            top_segments = [
                x for x in scored_segments
                if round(x[3], precision) == rounded_best
            ]
            return top_segments, best_score

        def find_nearest_anchor(
            candidate_start: int,
            candidate_end: int,
            top_segments: list[tuple[str, int, int, float, str]],
        ):
            """
            從 top anchor segments 中找離 candidate 最近的那個
            """
            if not top_segments:
                return None

            return min(
                top_segments,
                key=lambda x: distance_to_anchor(candidate_start, candidate_end, x[1], x[2])
            )

        def extract_otp_candidates(text: str, segments: list[tuple[str, int, int]]) -> list[dict]:

            # --- 1. 找出 URL 範圍（過濾用 + distance 折疊用）---
            url_ranges: list[tuple[int, int]] = []
            for url in self.url_extractor.find_urls(text):
                if not url.startswith(("http://", "https://")):
                    continue
                search_from = 0
                while True:
                    idx = text.find(url, search_from)
                    if idx == -1:
                        break
                    url_ranges.append((idx, idx + len(url)))
                    search_from = idx + len(url)

            # --- 2. 找出 noise 範圍（過濾用）---
            noise_ranges: list[tuple[int, int]] = [
                (m.start(), m.end())
                for p in self.noise_patterns
                for m in re.finditer(p, text)
            ]

            excluded_ranges = url_ranges + noise_ranges

            def is_in_excluded(start: int, end: int) -> bool:
                return any(s <= start and end <= e for s, e in excluded_ranges)

            def dist_collapsing_urls(cand_start: int, cand_end: int, anchor_start: int, anchor_end: int) -> int:
                if distance_to_anchor(cand_start, cand_end, anchor_start, anchor_end) == 0:
                    return 0
                gap_start = cand_end if cand_end <= anchor_start else anchor_end
                gap_end   = anchor_start if cand_end <= anchor_start else cand_start
                gap_size  = gap_end - gap_start
                if gap_size <= 0:
                    return 0

                # 合併重疊的 url_ranges，避免重複計算
                merged = []
                for s, e in sorted(url_ranges):
                    s = max(s, gap_start)
                    e = min(e, gap_end)
                    if s >= e:
                        continue
                    if merged and s <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], e))
                    else:
                        merged.append([s, e])

                url_chars = sum(e - s for s, e in merged)
                url_count = len(merged)
                non_url_chars = gap_size - url_chars
                return max(non_url_chars + url_count, 1)

            # --- 3. 找 OTP match，排除落在 URL / noise 裡的 ---
            all_otp_regex = list(self.otp_pattern.finditer(text))
            print("Regex raw matches:", [m.group(0) for m in all_otp_regex])
            for m in all_otp_regex:
                if is_in_excluded(m.start(), m.end()):
                    hit = next((r for r in excluded_ranges if r[0] <= m.start() and m.end() <= r[1]), None)
                    print(f"Excluded by range {hit}: {m.group(0)} at [{m.start()},{m.end()}]")
            otp_matches = [m for m in all_otp_regex if not is_in_excluded(m.start(), m.end())]
            print("OTP candidates:", [m.group(0) for m in otp_matches])

            # --- 4. 對所有 segment 算 anchor score ---
            otp_scored_segments = precompute_scored_segments(
                segments,
                self.otp_template_embeddings,
                self.otp_templates,
            )
            top_otp_segments, best_otp_anchor_score = get_top_scored_segments(otp_scored_segments, precision=4)
            print(
                "Top OTP anchor segments:",
                [{"text": (t[:60] + "…") if len(t) > 60 else t, "score": round(sc, 3), "template": tmpl}
                 for t, s, e, sc, tmpl in top_otp_segments]
            )

            if (
                self.notification_margin is not None
                and best_notification_score >= best_otp_anchor_score + self.notification_margin
            ):
                print(
                    f"Notification score {best_notification_score:.4f} >= "
                    f"OTP score {best_otp_anchor_score:.4f} + margin "
                    f"{self.notification_margin:.4f}; skip OTP selection."
                )
                return []

            if not (otp_matches and top_otp_segments and best_otp_anchor_score >= self.anchor_threshold):
                if otp_matches and best_otp_anchor_score < self.anchor_threshold:
                    print(f"Best OTP anchor score {best_otp_anchor_score:.4f} < threshold {self.anchor_threshold:.4f}, skip OTP selection.")
                return []

            # --- 5. 每個 OTP match 配最近的 anchor，算折疊後距離 ---
            best_by_code: dict[str, dict] = {}

            for match in otp_matches:
                code = match.group(0).replace(" ", "")
                cand_start, cand_end = match.start(), match.end()

                nearest_anchor = min(
                    top_otp_segments,
                    key=lambda x: dist_collapsing_urls(cand_start, cand_end, x[1], x[2]),
                )
                anchor_text, anchor_start, anchor_end, anchor_score, anchor_template = nearest_anchor
                dist = dist_collapsing_urls(cand_start, cand_end, anchor_start, anchor_end)

                print(
                    f"OTP candidate: {code}, "
                    f"anchor={anchor_text}, "
                    f"anchor_range=({anchor_start}, {anchor_end}), "
                    f"anchor_score={anchor_score:.4f}, "
                    f"dist={dist}, "
                    f"template={anchor_template}"
                )

                if dist > self.max_otp_anchor_distance:
                    print(f"Skip OTP candidate {code}: dist {dist} too far from anchor.")
                    continue

                candidate = {
                    "code": code,
                    "cand_start": cand_start,
                    "cand_end": cand_end,
                    "dist": dist,
                    "anchor_text": anchor_text,
                    "anchor_start": anchor_start,
                    "anchor_end": anchor_end,
                    "anchor_score": anchor_score,
                    "anchor_template": anchor_template,
                }
                if code not in best_by_code or dist < best_by_code[code]["dist"]:
                    best_by_code[code] = candidate

            # --- 6. 排序取前 3 ---
            otp_candidates = sorted(
                best_by_code.values(),
                key=lambda x: (x["dist"], -x["anchor_score"], x["cand_start"]),
            )[:3]
            print("Top-3 OTP candidates:", otp_candidates)
            return otp_candidates

        def extract_url_value(text: str, segments: list[tuple[str, int, int]]) -> str | None:
            url_value = None

            urls = self.url_extractor.find_urls(text)
            urls = [u for u in urls if isinstance(u, str) and u.startswith(("http://", "https://"))]
            print("URL candidates:", [(u[:60] + "…") if len(u) > 60 else u for u in urls])

            url_scored_segments = precompute_scored_segments(
                segments,
                self.url_template_embeddings,
                self.url_templates
            )

            top_url_segments, best_url_anchor_score = get_top_scored_segments(
                url_scored_segments,
                precision=4,
            )

            if (
                self.notification_margin is not None
                and best_notification_score >= best_url_anchor_score + self.notification_margin
            ):
                print(
                    f"Notification score {best_notification_score:.4f} >= "
                    f"URL score {best_url_anchor_score:.4f} + margin "
                    f"{self.notification_margin:.4f}; skip URL selection."
                )
                return None

            print(
                "Top URL anchor segments:",
                [
                    {
                        "text": (seg_text[:60] + "…") if len(seg_text) > 60 else seg_text,
                        "range": (seg_start, seg_end),
                        "score": score,
                        "template": matched_template,
                    }
                    for seg_text, seg_start, seg_end, score, matched_template in top_url_segments
                ]
            )

            if urls and top_url_segments and best_url_anchor_score >= self.anchor_threshold:
                best_url = None
                best_dist = float("inf")
                best_anchor_text = None
                best_anchor_score = 0.0
                best_anchor_range = None
                best_template = None

                for url in urls:
                    lower_url = url.lower()
                    if any(k in lower_url for k in ("login", "verify", "auth", "confirm")):
                        print(f"[DIRECT KEYWORD URL HIT] {url}")
                        best_url = url
                        best_dist = -1
                        best_anchor_text = "[DIRECT MATCH]"
                        best_anchor_score = 1.0
                        best_anchor_range = None
                        best_template = "keyword_override"
                        break

                    cand_start = text.find(url)
                    if cand_start == -1:
                        continue
                    cand_end = cand_start + len(url)

                    nearest_anchor = find_nearest_anchor(
                        cand_start,
                        cand_end,
                        top_url_segments
                    )
                    if nearest_anchor is None:
                        continue

                    anchor_text, anchor_start, anchor_end, anchor_score, anchor_template = nearest_anchor
                    dist = distance_to_anchor(cand_start, cand_end, anchor_start, anchor_end)

                    print(
                        f"URL candidate: {(url[:60] + '…') if len(url) > 60 else url}, "
                        f"anchor={(anchor_text[:60] + '…') if len(anchor_text) > 60 else anchor_text}, "
                        f"anchor_range=({anchor_start}, {anchor_end}), "
                        f"anchor_score={anchor_score:.4f}, "
                        f"dist={dist}, "
                        f"template={anchor_template}"
                    )

                    if dist < best_dist:
                        best_url = url
                        best_dist = dist
                        best_anchor_text = anchor_text
                        best_anchor_score = anchor_score
                        best_anchor_range = (anchor_start, anchor_end)
                        best_template = anchor_template

                if best_url is not None:
                    print(
                        "Best URL:",
                        (
                            (best_url[:60] + "…") if len(best_url) > 60 else best_url,
                            best_dist,
                            best_template,
                            (best_anchor_text[:60] + "…") if best_anchor_text and len(best_anchor_text) > 60 else best_anchor_text,
                            best_anchor_score,
                            best_anchor_range,
                        )
                    )
                    url_value = best_url
            else:
                if urls and best_url_anchor_score < self.anchor_threshold:
                    print(
                        f"Best URL anchor score {best_url_anchor_score:.4f} < threshold {self.anchor_threshold:.4f}, skip URL selection."
                    )
            return url_value

        if source == "email" or source == "sms":
            if not message_data:
                raise ValueError("message_data is None for email/sms source")

            text = message_data.get("text", "") or ""

            # 統一換行
            text = text.replace('\r\n', '\n').replace('\r', '\n')
            # 每行去頭尾空白
            text = '\n'.join(line.strip() for line in text.split('\n'))
            # 壓掉多餘空行
            text = re.sub(r'\n+', '\n', text).strip()

            # 中字和數字中間補空白
            text = re.sub(
                r'(?<=\d)(?=[\u4e00-\u9fff])|(?<=[\u4e00-\u9fff])(?=\d)',
                ' ',
                text
            )

            segments = split_segments(text)
            print("Segments:", [(s[0][:60] + "…") if len(s[0]) > 60 else s[0] for s in segments])

            best_notification_score = 0.0
            if self.notification_template_embeddings is not None:
                notification_scored_segments = precompute_scored_segments(
                    segments,
                    self.notification_template_embeddings,
                    self.notification_templates,
                )
                _, best_notification_score = get_top_scored_segments(
                    notification_scored_segments,
                    precision=4,
                )
                print(f"Best notification score: {best_notification_score:.4f}")

            otp_candidates = []
            url_value = None
            if credential_preference == "otp":
                otp_candidates = extract_otp_candidates(text, segments)
            elif credential_preference in ["otp+link", "none"]:
                url_value = extract_url_value(text, segments)
                if not url_value:
                    otp_candidates = extract_otp_candidates(text, segments)
            elif credential_preference in ["link"]:
                url_value = extract_url_value(text, segments)
            else:
                pass

        elif source == "app":
            if not self.TOTP_SECRET:
                raise RuntimeError("TOTP_SECRET not set")
            totp = pyotp.TOTP(self.TOTP_SECRET)
            otp_candidates = [
                {
                    "code": totp.now(),
                    "cand_start": -1,
                    "cand_end": -1,
                    "dist": 0,
                    "anchor_text": "TOTP",
                    "anchor_start": -1,
                    "anchor_end": -1,
                    "anchor_score": 1.0,
                    "anchor_template": "TOTP",
                }
            ]
            url_value = None

        print("Final OTP:", [c["code"] for c in otp_candidates])
        print("Final URL:", (url_value[:60] + "…") if url_value and len(url_value) > 60 else url_value)
        return otp_candidates, url_value

    @staticmethod
    def extract_totp_secret_from_otpauth_uri(uri: str) -> str | None:
        if not uri or not uri.startswith("otpauth://"):
            return None
        parsed = urlparse(uri)
        qs = parse_qs(parsed.query)
        secret = qs.get("secret", [None])[0]
        if not secret:
            return None
        secret = secret.replace(" ", "").upper().strip()
        return secret or None

    @staticmethod
    def extract_totp_secret_from_qr_png(png_bytes: bytes) -> str | None:
        """
        從目前頁面 screenshot 的 PNG bytes 裡偵測 QR code，
        如果 QR code 內容是 otpauth://totp/...?...secret=xxx，
        就回傳 secret。
        """
        if not png_bytes:
            return None
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        detector = cv2.QRCodeDetector()
        detected_points = []
        # 先試單一 QR
        data, points, _ = detector.detectAndDecode(img)
        if points is not None:
            detected_points.append(points)
        if data:
            secret = OTPCodeExtractor.extract_totp_secret_from_otpauth_uri(data)
            if secret:
                print(f"TOTP Secret:{secret}")
                return secret
        # 再試多 QR
        try:
            ok, decoded_info, points, _ = detector.detectAndDecodeMulti(img)
            if points is not None:
                for p in points:
                    detected_points.append(p)
            if ok:
                for data in decoded_info:
                    secret = OTPCodeExtractor.extract_totp_secret_from_otpauth_uri(data)
                    if secret:
                        print(f"TOTP Secret:{secret}")
                        return secret
        except Exception:
            pass

        # QR 太小導致完全偵測不到：先把整張圖放大後重試
        if not detected_points:
            for scale in [2, 4]:
                scaled = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
                data, points, _ = detector.detectAndDecode(scaled)
                if points is not None:
                    detected_points.append(points)
                if data:
                    secret = OTPCodeExtractor.extract_totp_secret_from_otpauth_uri(data)
                    if secret:
                        print(f"TOTP Secret (scaled {scale}x): {secret}")
                        return secret
                try:
                    ok, decoded_info, points, _ = detector.detectAndDecodeMulti(scaled)
                    if points is not None:
                        for p in points:
                            detected_points.append(p)
                    if ok:
                        for data in decoded_info:
                            secret = OTPCodeExtractor.extract_totp_secret_from_otpauth_uri(data)
                            if secret:
                                print(f"TOTP Secret (scaled {scale}x multi): {secret}")
                                return secret
                except Exception:
                    pass
                if detected_points:
                    break

        # 有偵測到 QR 位置，但解不出 data：用 points 自動裁切、放大、補白邊後再試
        for points in detected_points:
            pts = points.reshape(-1, 2).astype(int)
            padding = 40
            x1 = max(pts[:, 0].min() - padding, 0)
            y1 = max(pts[:, 1].min() - padding, 0)
            x2 = min(pts[:, 0].max() + padding, img.shape[1])
            y2 = min(pts[:, 1].max() + padding, img.shape[0])
            crop = img[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            crop = cv2.resize(
                crop,
                None,
                fx=8,
                fy=8,
                interpolation=cv2.INTER_NEAREST
            )
            crop = cv2.copyMakeBorder(
                crop,
                120, 120, 120, 120,
                cv2.BORDER_CONSTANT,
                value=[255, 255, 255]
            )
            # 單一 QR
            data, _, _ = detector.detectAndDecode(crop)
            if data:
                secret = OTPCodeExtractor.extract_totp_secret_from_otpauth_uri(data)
                if secret:
                    print(f"TOTP Secret:{secret}")
                    return secret
            # 多 QR
            try:
                ok, decoded_info, _, _ = detector.detectAndDecodeMulti(crop)
                if ok:
                    for data in decoded_info:
                        if not data:
                            continue
                        secret = OTPCodeExtractor.extract_totp_secret_from_otpauth_uri(data)
                        if secret:
                            print(f"TOTP Secret:{secret}")
                            return secret
            except Exception:
                pass
        return None

if __name__ == "__main__":
    import json
    extractor = OTPCodeExtractor(embedding_model=None)

    # 格式：{"text": "...", "expected": "482913", "source": "email", "credential_preference": "otp"}
    # expected 填 None 表示這筆不應該提取到任何 OTP（negative sample）
    test_cases = [
        {"text": "Your verification code is 482913. Please enter this code to complete your login.", "expected": "482913", "source": "email", "credential_preference": "otp"},
    ]

    top1_hit = 0
    top3_hit = 0
    url_hit = 0
    true_negative = 0  # expected=None 且確實沒提取到
    false_positive = 0  # expected=None 但提取到了
    total = len(test_cases)
    errors = []

    for i, case in enumerate(test_cases):
        text = case["text"]
        expected = case.get("expected")        # OTP 碼 或 URL 字串
        source = case.get("source", "email")
        pref = case.get("credential_preference", "otp")
        is_url_case = isinstance(expected, str) and expected.startswith("http")

        try:
            otp_candidates, url_value = extractor.extract_otp_code({"text": text}, source, pref)

            if expected is None:
                got_anything = bool(otp_candidates) or bool(url_value)
                if not got_anything:
                    true_negative += 1
                else:
                    false_positive += 1
                    errors.append({"index": i, "expected": None, "got_otp": [c["code"] for c in otp_candidates], "got_url": url_value, "text": text[:80]})

            elif is_url_case:
                if url_value and expected in url_value:
                    url_hit += 1
                else:
                    errors.append({"index": i, "expected_url": expected, "got_url": url_value, "text": text[:80]})

            else:
                codes = [c["code"].replace(" ", "") for c in otp_candidates]
                expected_clean = expected.replace(" ", "")
                if codes and codes[0] == expected_clean:
                    top1_hit += 1
                    top3_hit += 1
                elif expected_clean in codes[:3]:
                    top3_hit += 1
                    errors.append({"index": i, "expected": expected_clean, "top1": codes[0] if codes else None, "text": text[:80]})
                else:
                    errors.append({"index": i, "expected": expected_clean, "got_top3": codes[:3], "text": text[:80]})

        except Exception as e:
            errors.append({"index": i, "error": str(e), "text": text[:80]})

    otp_positive_total = sum(1 for c in test_cases if c.get("expected") is not None and not str(c.get("expected", "")).startswith("http"))
    url_positive_total = sum(1 for c in test_cases if isinstance(c.get("expected"), str) and c["expected"].startswith("http"))
    negative_total = sum(1 for c in test_cases if c.get("expected") is None)

    print("\n===== Evaluation Results =====")
    print(f"Total samples      : {total}")
    print(f"OTP positive       : {otp_positive_total}")
    print(f"URL positive       : {url_positive_total}")
    print(f"Negative samples   : {negative_total}")
    print(f"OTP Top-1 accuracy : {top1_hit}/{otp_positive_total} = {top1_hit/otp_positive_total*100:.1f}%" if otp_positive_total else "OTP Top-1: N/A")
    print(f"OTP Top-3 accuracy : {top3_hit}/{otp_positive_total} = {top3_hit/otp_positive_total*100:.1f}%" if otp_positive_total else "OTP Top-3: N/A")
    print(f"URL accuracy       : {url_hit}/{url_positive_total} = {url_hit/url_positive_total*100:.1f}%" if url_positive_total else "URL: N/A")
    print(f"True negative      : {true_negative}/{negative_total} = {true_negative/negative_total*100:.1f}%" if negative_total else "True negative: N/A")
    print(f"False positive     : {false_positive}/{negative_total}" if negative_total else "False positive: N/A")
    if errors:
        print(f"\nFailed cases ({len(errors)}):")
        for e in errors:
            print(" ", json.dumps(e, ensure_ascii=False))
