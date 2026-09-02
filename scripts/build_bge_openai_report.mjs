import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const inputPath = option("--input", path.join(ROOT, "results", "bge_openai", "bge_openai_dry_run.json"));
const outputPath = option(
  "--output",
  path.join(ROOT, "outputs", "bge_openai_report", "bge_openai_comparison.xlsx"),
);
const previewDir = option(
  "--preview-dir",
  "C:/Users/Owner/AppData/Local/Temp/codex_bge_openai_report_previews",
);
const language = option("--lang", "en");
const zh = language.toLowerCase().startsWith("zh");

const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const sheetNames = zh
  ? { summary: "摘要", calls: "API 呼叫", misses: "遺漏與錯誤", detail: "全部明細" }
  : { summary: "Summary", calls: "API Calls", misses: "Misses and Errors", detail: "All Detail" };

const metricLabels = {
  "OTP Top-1": zh ? "OTP Top-1 正確率" : "OTP Top-1",
  "OTP Top-3": zh ? "OTP Top-3 正確率" : "OTP Top-3",
  "Verification Link": zh ? "驗證連結擷取率" : "Verification Link",
  "Negative False Extraction": zh ? "負樣本誤抽率" : "Negative False Extraction",
};
const routeLabels = zh ? {
  OPENAI_FALLBACK: "OpenAI 輔助判斷",
  BGE_DIRECT: "BGE 直接輸出",
  DRY_RUN_OPENAI_FALLBACK_TO_BGE: "Dry-run：應呼叫 OpenAI，暫用 BGE",
  API_LIMIT_FALLBACK_TO_BGE: "達 API 上限，改用 BGE",
  API_ERROR_FALLBACK_TO_BGE: "API 錯誤，改用 BGE",
} : {};
const reasonLabels = zh ? {
  NO_REGEX_CANDIDATE: "Regex 沒有候選",
  BGE_CONFIDENT: "BGE 信心充足",
  MULTIPLE_CANDIDATES: "有多個候選",
  STRUCTURAL_YEAR: "候選疑似年份",
  STRUCTURAL_ADDRESS: "候選位於地址資訊",
  STRUCTURAL_PHONE_SUFFIX: "候選疑似電話末碼",
  UPPERCASE_ALPHA_CANDIDATE: "候選為純大寫英文字串",
  LOW_BGE_SCORE_WITH_CODE_CUE: "有驗證碼語句但 BGE 分數低",
  BGE_CANDIDATE_VS_NO_OTP_UNCERTAIN: "BGE 無法確定候選或 NO_OTP",
  BGE_MIXED_OTP_NOTIFICATION: "OTP 與安全通知語意同時存在",
  TOP1_TOP2_CLOSE: "Top-1 與 Top-2 分數接近",
  TOP1_TOP3_CLOSE: "Top-1 與 Top-3 分數接近",
} : {};
const messageTypeLabels = zh ? {
  OTP: "一次性驗證碼",
  SECURITY_NOTIFICATION: "安全通知",
  PROMOTION: "促銷訊息",
  OTHER_NO_OTP: "其他非 OTP 訊息",
  UNCERTAIN: "不確定",
} : {};
const apiReasonLabels = zh ? {
  EXPLICIT_ACTIONABLE_OTP: "明確且可使用的 OTP",
  OTP_WITH_DISTRACTORS: "OTP 周圍含干擾候選",
  PROMO_CODE: "促銷代碼",
  PHONE_SUFFIX: "電話末碼",
  ADDRESS_OR_FOOTER: "地址或頁尾資訊",
  STATUS_NOTIFICATION: "狀態通知",
  NO_ACTIONABLE_OTP: "沒有可使用的 OTP",
  AMBIGUOUS: "語意不明確",
} : {};
const categoryLabels = zh ? { otp: "OTP", link: "驗證連結", negative: "負樣本" } : {};
const sourceLabels = zh ? { email: "電子郵件", sms: "SMS", link: "連結" } : {};
const label = (value, labels) => labels[value] ?? value;
const reasonList = (values) => values.map((value) => label(value, reasonLabels)).join("、");

const wb = Workbook.create();
const summary = wb.worksheets.add(sheetNames.summary);
const calls = wb.worksheets.add(sheetNames.calls);
const misses = wb.worksheets.add(sheetNames.misses);
const detail = wb.worksheets.add(sheetNames.detail);

const C = {
  ink: "#17324D",
  teal: "#0F766E",
  green: "#15803D",
  greenLight: "#DCFCE7",
  orange: "#C2410C",
  orangeLight: "#FFEDD5",
  red: "#B91C1C",
  redLight: "#FEE2E2",
  blueLight: "#DBEAFE",
  gray: "#CBD5E1",
  grayLight: "#F1F5F9",
  white: "#FFFFFF",
};

function title(sheet, address, value) {
  sheet.getRange(address).merge();
  const anchor = sheet.getRange(address.split(":")[0]);
  anchor.values = [[value]];
  anchor.format = {
    fill: C.ink,
    font: { color: C.white, bold: true, size: 18 },
    verticalAlignment: "center",
  };
  sheet.getRange(address).format.rowHeight = 28;
}

function header(range) {
  range.format = {
    fill: C.teal,
    font: { color: C.white, bold: true },
    borders: { preset: "all", style: "thin", color: C.gray },
    wrapText: true,
    verticalAlignment: "center",
  };
}

function grid(range) {
  range.format = {
    borders: { preset: "all", style: "thin", color: C.gray },
    wrapText: true,
    verticalAlignment: "top",
  };
}

function table(sheet, address, name) {
  const item = sheet.tables.add(address, true, name);
  item.style = "TableStyleMedium2";
  item.showBandedRows = true;
  item.showFilterButton = true;
}

for (const sheet of [summary, calls, misses, detail]) {
  sheet.showGridLines = false;
}

const metricMap = {
  "OTP Top-1": data.metrics.otp_top1,
  "OTP Top-3": data.metrics.otp_top3,
  "Verification Link": data.metrics.verification_link,
  "Negative False Extraction": data.metrics.negative_false_extraction,
};
const comparisonRows = data.comparison.map((row) => {
  const hybrid = metricMap[row.metric];
  return [
    metricLabels[row.metric],
    row.metric === "Negative False Extraction"
      ? (zh ? "越低越好" : "Lower is better")
      : (zh ? "越高越好" : "Higher is better"),
    row.baseline_hits,
    row.baseline_total,
    null,
    row.regex_v2_hits,
    row.regex_v2_total,
    null,
    hybrid.hits,
    hybrid.total,
    null,
    null,
  ];
});

const isDryRun = Boolean(data.experiment?.dry_run);
const runLabel = isDryRun
  ? (zh ? "Dry-run（未呼叫 OpenAI）" : "Dry Run (OpenAI not called)")
  : (zh ? "正式 API 實驗" : "API Run");
title(summary, "A1:L2", zh ? "Regex v2 + BGE + OpenAI 輔助實驗" : "Regex v2 + BGE + OpenAI Fallback Experiment");
summary.getRange("A3:L3").merge();
summary.getRange("A3").values = [[
  zh
    ? `${runLabel} | 模型：${data.experiment.openai.model} | 資料：${data.detail.length} 筆 | API 呼叫：${data.api_usage.api_calls} 次`
    : `${runLabel} | Model: ${data.experiment.openai.model} | Rows: ${data.detail.length} | API calls: ${data.api_usage.api_calls}`,
]];
summary.getRange("A3:L3").format = { fill: C.grayLight, font: { italic: true, color: C.ink } };
summary.getRange("A5:L5").values = [zh ? [
  "指標", "判讀方向", "Baseline 正確數", "Baseline 樣本數", "Baseline 比率",
  "Regex v2+BGE 正確數", "Regex v2+BGE 樣本數", "Regex v2+BGE 比率", "新流程正確數", "新流程樣本數",
  "新流程比率", "相較 BGE 差異",
] : [
  "Metric", "Direction", "Baseline Hits", "Baseline Total", "Baseline Rate",
  "Regex v2+BGE Hits", "Regex v2+BGE Total", "Regex v2+BGE Rate", "Pipeline Hits", "Pipeline Total",
  "Pipeline Rate", "Delta vs BGE",
]];
summary.getRangeByIndexes(5, 0, comparisonRows.length, comparisonRows[0].length).values = comparisonRows;
header(summary.getRange("A5:L5"));
grid(summary.getRange(`A6:L${comparisonRows.length + 5}`));
for (let row = 6; row <= comparisonRows.length + 5; row += 1) {
  summary.getRange(`E${row}`).formulas = [[`=C${row}/D${row}`]];
  summary.getRange(`H${row}`).formulas = [[`=F${row}/G${row}`]];
  summary.getRange(`K${row}`).formulas = [[`=I${row}/J${row}`]];
  summary.getRange(`L${row}`).formulas = [[`=K${row}-H${row}`]];
}
summary.getRange(`E6:E${comparisonRows.length + 5}`).format.numberFormat = "0.0%";
summary.getRange(`H6:H${comparisonRows.length + 5}`).format.numberFormat = "0.0%";
summary.getRange(`K6:K${comparisonRows.length + 5}`).format.numberFormat = "0.0%";
summary.getRange(`L6:L${comparisonRows.length + 5}`).format.numberFormat = "+0.0%;-0.0%;0.0%";
summary.getRange("K6:K8").format.fill = C.greenLight;
summary.getRange("K9").format.fill = C.orangeLight;

summary.getRange("A12:D12").values = [[...(zh ? ["API 用量", "數值", "單位", "備註"] : ["API Usage", "Value", "Unit", "Notes"])]];
header(summary.getRange("A12:D12"));
const usageRows = zh ? [
  ["API 嘗試呼叫次數", data.api_usage.api_calls, "次", isDryRun ? "Dry-run 不會呼叫 API" : "送往 OpenAI 的請求"],
  ["API 成功次數", data.api_usage.successful_calls, "次", "取得有效結果與用量紀錄"],
  ["API 失敗次數", data.api_usage.failed_calls, "次", "未取得有效判斷的呼叫"],
  ["送交 OpenAI 的訊息", data.detail.filter((row) => row.route.includes("OPENAI")).length, "筆", isDryRun ? "Dry-run 預計呼叫數" : "由 OpenAI 判斷的訊息數"],
  ["輸入 tokens", data.api_usage.input_tokens, "tokens", "包含快取與 cache-write tokens"],
  ["快取輸入 tokens", data.api_usage.cached_input_tokens, "tokens", "API 回報值"],
  ["Cache-write tokens", data.api_usage.cache_write_tokens, "tokens", "API 回報值"],
  ["輸出 tokens", data.api_usage.output_tokens, "tokens", "包含結構化輸出"],
  ["推理 tokens", data.api_usage.reasoning_tokens, "tokens", "API 回報值"],
  ["總 tokens", data.api_usage.total_tokens, "tokens", "輸入與輸出的總用量"],
  ["估計總成本", data.api_usage.estimated_cost_usd, "美元", "依 .env 所存費率估算"],
  ["每 1,000 筆估計成本", data.api_usage.estimated_cost_per_1000_messages_usd, "美元", "依本次實驗等比例推估"],
  ["平均 API 延遲", data.api_usage.average_latency_ms, "毫秒", "僅計算成功呼叫"],
] : [
  ["API calls attempted", data.api_usage.api_calls, "requests", isDryRun ? "Zero during dry-run" : "Requests sent to OpenAI"],
  ["Successful API calls", data.api_usage.successful_calls, "requests", "Responses with usage records"],
  ["Failed API calls", data.api_usage.failed_calls, "requests", "Attempted calls without a valid decision"],
  ["Rows gated to OpenAI", data.detail.filter((row) => row.route.includes("OPENAI")).length, "messages", isDryRun ? "Would-call count during dry-run" : "Messages adjudicated by OpenAI"],
  ["Input tokens", data.api_usage.input_tokens, "tokens", "Includes cached and cache-write tokens"],
  ["Cached input tokens", data.api_usage.cached_input_tokens, "tokens", "Reported by the API"],
  ["Cache-write tokens", data.api_usage.cache_write_tokens, "tokens", "Reported by the API"],
  ["Output tokens", data.api_usage.output_tokens, "tokens", "Includes visible structured output"],
  ["Reasoning tokens", data.api_usage.reasoning_tokens, "tokens", "Reported by the API"],
  ["Total tokens", data.api_usage.total_tokens, "tokens", "Input plus output accounting"],
  ["Estimated total cost", data.api_usage.estimated_cost_usd, "USD", "Uses rates saved in .env"],
  ["Estimated cost / 1,000 messages", data.api_usage.estimated_cost_per_1000_messages_usd, "USD", "Scaled from this run"],
  ["Average API latency", data.api_usage.average_latency_ms, "ms", "Successful calls only"],
];
summary.getRangeByIndexes(12, 0, usageRows.length, 4).values = usageRows;
grid(summary.getRange(`A13:D${usageRows.length + 12}`));
summary.getRange("B23:B24").format.numberFormat = "$0.000000";

summary.getRange("F12:H12").values = [[...(zh ? ["處理路徑", "筆數", "占比"] : ["Route", "Rows", "Share"])]];
header(summary.getRange("F12:H12"));
const routeEntries = Object.entries(data.experiment.routing);
summary.getRangeByIndexes(12, 5, routeEntries.length, 3).values = routeEntries.map(([route, count]) => [label(route, routeLabels), count, null]);
for (let row = 13; row < 13 + routeEntries.length; row += 1) {
  summary.getRange(`H${row}`).formulas = [[`=G${row}/${data.detail.length}`]];
}
grid(summary.getRange(`F13:H${routeEntries.length + 12}`));
summary.getRange(`H13:H${routeEntries.length + 12}`).format.numberFormat = "0.0%";

summary.getRange("J12:L12").values = [[...(zh ? ["指標", "Regex v2+BGE", "新流程"] : ["Metric", "Regex v2+BGE", "Pipeline"])]];
header(summary.getRange("J12:L12"));
summary.getRange("J13:J16").values = comparisonRows.map((row) => [row[0]]);
summary.getRange("K13:K16").formulas = [["=H6"], ["=H7"], ["=H8"], ["=H9"]];
summary.getRange("L13:L16").formulas = [["=K6"], ["=K7"], ["=K8"], ["=K9"]];
summary.getRange("K13:L16").format.numberFormat = "0.0%";
const chart = summary.charts.add("bar", summary.getRange("J12:L16"));
chart.title = data.experiment?.dry_run
  ? (zh ? "Dry-run 與 Regex v2+BGE 結果一致" : "Dry-run parity with Regex v2+BGE")
  : (zh ? "Regex v2+BGE 與 OpenAI 輔助流程比較" : "Regex v2+BGE vs OpenAI-assisted pipeline");
chart.hasLegend = true;
chart.yAxis = { numberFormatCode: "0%", min: 0, max: 1 };
chart.setPosition("F20", "L34");

summary.getRange("A29:D33").merge();
summary.getRange("A29").values = [[
  data.experiment?.dry_run
    ? (zh
      ? "Dry-run 說明：此模式只測試 API gate，所有預計呼叫 API 的資料仍沿用原始 BGE 答案。因此準確率必須與 Regex v2+BGE 相同，不能視為 OpenAI 實驗結果。Ground truth 僅在預測完成後用於計分。"
      : "Dry-run note: the API gate was evaluated, but every would-call row retained the original BGE answer. Accuracy therefore matches Regex v2+BGE and is not an OpenAI result. Ground truth was used only after prediction for scoring.")
    : (zh
      ? "評估說明：Ground truth 僅在預測完成後用於計分，沒有參與路由或模型判斷。由於這批資料曾用於前期開發，若要主張泛化能力，仍應使用未參與開發的 held-out 測試集再次驗證。"
      : "Evaluation note: ground truth was used only after prediction for scoring. Because this dataset informed earlier development, confirm any improvement on a held-out dataset before making a generalization claim."),
]];
summary.getRange("A29:D33").format = {
  fill: C.blueLight,
  font: { color: C.ink, bold: true },
  borders: { preset: "outside", style: "medium", color: C.ink },
  wrapText: true,
  verticalAlignment: "center",
};
summary.freezePanes.freezeRows(3);
summary.getRange("A:A").format.columnWidth = 30;
summary.getRange("B:L").format.columnWidth = 16;
summary.getRange("D:D").format.columnWidth = 34;
summary.getRange("J:J").format.columnWidth = 24;

const detailHeaders = zh ? [
  "資料 ID", "類別", "來源", "處理路徑", "路由原因", "候選值", "正確答案",
  "BGE Top-1", "BGE Top-3", "最終 Top-1", "最終 Top-3", "Top-1 正確", "Top-3 正確",
  "連結正確", "負樣本誤抽", "已呼叫 API", "API 訊息類型", "API 判斷原因",
  "輸入 tokens", "輸出 tokens", "總 tokens", "成本（美元）", "延遲（毫秒）", "API 錯誤",
] : [
  "Dataset ID", "Category", "Source", "Route", "Route Reasons", "Candidates", "Ground Truth",
  "BGE Top-1", "BGE Top-3", "Final Top-1", "Final Top-3", "Top-1 Correct", "Top-3 Correct",
  "Link Correct", "False Extraction", "API Attempted", "API Message Type", "API Reason",
  "Input Tokens", "Output Tokens", "Total Tokens", "Cost USD", "Latency ms", "API Error",
];
const detailRows = data.detail.map((row) => [
  row.dataset_id,
  label(row.category, categoryLabels),
  label(row.source, sourceLabels),
  label(row.route, routeLabels),
  reasonList(row.route_reasons),
  row.candidates.map((candidate) => `${candidate.candidate_id}:${candidate.value}`).join(", "),
  row.ground_truth,
  row.bge_top1,
  row.bge_top3.join(", "),
  row.final_top1,
  row.final_top3.join(", "),
  row.top1_correct,
  row.top3_correct,
  row.link_correct,
  row.false_extraction,
  row.api_attempted ? 1 : 0,
  label(row.api?.message_type ?? "", messageTypeLabels),
  label(row.api?.reason_code ?? "", apiReasonLabels),
  row.api?.usage?.input_tokens ?? 0,
  row.api?.usage?.output_tokens ?? 0,
  row.api?.usage?.total_tokens ?? 0,
  row.api?.usage?.estimated_cost_usd ?? 0,
  row.api?.latency_ms ?? 0,
  row.api_error,
]);
detail.getRangeByIndexes(0, 0, detailRows.length + 1, detailHeaders.length).values = [detailHeaders, ...detailRows];
table(detail, `A1:X${detailRows.length + 1}`, "BGEOpenAIAllDetailTable");
header(detail.getRange("A1:X1"));
grid(detail.getRange(`A2:X${detailRows.length + 1}`));
detail.freezePanes.freezeRows(1);
detail.freezePanes.freezeColumns(3);
detail.getRange("A:D").format.columnWidth = 15;
detail.getRange("E:K").format.columnWidth = 28;
detail.getRange("L:X").format.columnWidth = 15;
detail.getRange(`A2:X${detailRows.length + 1}`).format.rowHeight = 32;
detail.getRange(`V2:V${detailRows.length + 1}`).format.numberFormat = "$0.00000000";
for (const col of ["L", "M", "N"]) {
  detail.getRange(`${col}2:${col}${detailRows.length + 1}`).conditionalFormats.add("cellIs", {
    operator: "equal", formula: 1, format: { fill: C.greenLight, font: { color: C.green, bold: true } },
  });
}
detail.getRange(`O2:O${detailRows.length + 1}`).conditionalFormats.add("cellIs", {
  operator: "equal", formula: 1, format: { fill: C.redLight, font: { color: C.red, bold: true } },
});

const apiRows = data.detail
  .map((row, index) => ({ row, values: detailRows[index] }))
  .filter(({ row }) => row.api_attempted || row.route.includes("OPENAI"))
  .map(({ values }) => values);
title(calls, "A1:X2", zh ? "OpenAI 判斷資料與用量" : "OpenAI Gate Rows and Usage");
calls.getRangeByIndexes(3, 0, apiRows.length + 1, detailHeaders.length).values = [detailHeaders, ...apiRows];
table(calls, `A4:X${apiRows.length + 4}`, "BGEOpenAIGateTable");
header(calls.getRange("A4:X4"));
grid(calls.getRange(`A5:X${apiRows.length + 4}`));
calls.freezePanes.freezeRows(4);
calls.getRange("A:D").format.columnWidth = 15;
calls.getRange("E:K").format.columnWidth = 28;
calls.getRange("L:X").format.columnWidth = 15;
calls.getRange(`A5:X${apiRows.length + 4}`).format.rowHeight = 42;
calls.getRange(`V5:V${apiRows.length + 4}`).format.numberFormat = "$0.00000000";

const missRows = data.detail
  .map((row, index) => ({ row, values: detailRows[index] }))
  .filter(({ row }) => (
    (row.category === "otp" && row.top3_correct === 0)
    || (row.category === "link" && row.link_correct === 0)
    || row.false_extraction === 1
    || row.api_error
  ))
  .map(({ values }) => values);
title(misses, "A1:X2", zh ? "剩餘遺漏、誤抽與 API 錯誤" : "Remaining Misses, False Extractions, and API Errors");
misses.getRangeByIndexes(3, 0, missRows.length + 1, detailHeaders.length).values = [detailHeaders, ...missRows];
table(misses, `A4:X${missRows.length + 4}`, "BGEOpenAIMissesTable");
header(misses.getRange("A4:X4"));
grid(misses.getRange(`A5:X${missRows.length + 4}`));
misses.freezePanes.freezeRows(4);
misses.getRange("A:D").format.columnWidth = 15;
misses.getRange("E:K").format.columnWidth = 28;
misses.getRange("L:X").format.columnWidth = 15;
misses.getRange(`A5:X${missRows.length + 4}`).format.rowHeight = 48;

const previewSpecs = [
  [sheetNames.summary, "A1:L34", "summary.png"],
  [sheetNames.calls, `A1:X${Math.min(apiRows.length + 4, 24)}`, "api_calls.png"],
  [sheetNames.misses, `A1:X${Math.min(missRows.length + 4, 24)}`, "misses.png"],
  [sheetNames.detail, "A1:X24", "detail.png"],
];
for (const [sheetName, range, filename] of previewSpecs) {
  const preview = await wb.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, filename), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(outputPath);
console.log(`Workbook: ${outputPath}`);
console.log(`Previews: ${previewDir}`);
