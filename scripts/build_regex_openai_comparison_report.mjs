import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const hybridPath = path.join(ROOT, "results", "bge_openai", "bge_openai_experiment_results.json");
const openaiPath = path.join(ROOT, "results", "regex_openai", "regex_openai_experiment_results.json");
const outputDir = path.join(ROOT, "outputs", "regex_openai_comparison");
const outputPath = path.join(outputDir, "Regex_OpenAI_外部成本比較_中文.xlsx");
const previewDir = path.join(outputDir, "preview");

const hybrid = JSON.parse(await fs.readFile(hybridPath, "utf8"));
const regexOpenAI = JSON.parse(await fs.readFile(openaiPath, "utf8"));
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const percentile = (values, p) => {
  if (!values.length) return 0;
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.max(0, Math.ceil(p * ordered.length) - 1)];
};
const shorten = (value, limit = 90) => {
  const text = String(value ?? "");
  return text.length <= limit ? text : `${text.slice(0, limit - 3)}...`;
};
const hybridLatencies = hybrid.detail.filter((row) => row.api).map((row) => row.api.latency_ms);
const hybridStats = {
  median: percentile(hybridLatencies, 0.5),
  p95: percentile(hybridLatencies, 0.95),
  max: Math.max(...hybridLatencies),
  total: hybridLatencies.reduce((sum, value) => sum + value, 0),
};

const hybridBySequence = new Map(hybrid.detail.map((row) => [row.sequence, row]));
const allRows = regexOpenAI.detail.map((row) => ({
  hybrid: hybridBySequence.get(row.sequence),
  openai: row,
}));
const notableRows = allRows.filter(({ hybrid: h, openai: o }) =>
  h.final_top3.join(",") !== o.final_top3.join(",") ||
  !o.top1_correct && o.category === "otp" ||
  !o.top3_correct && o.category === "otp" ||
  !o.link_correct && o.category === "link" ||
  o.false_extraction || h.api_error || o.api_error
);

const apiRows = [];
for (const { hybrid: h, openai: o } of allRows) {
  if (h.api_attempted) apiRows.push({ architecture: "Regex+BGE+OpenAI fallback", row: h });
  if (o.api_attempted) apiRows.push({ architecture: "Regex+OpenAI", row: o });
}

const wb = Workbook.create();
const summary = wb.worksheets.add("實驗摘要");
const costs = wb.worksheets.add("外部成本");
const differences = wb.worksheets.add("差異與遺漏");
const details = wb.worksheets.add("全部明細");
const calls = wb.worksheets.add("API 呼叫明細");
const notes = wb.worksheets.add("定義與來源");

const C = {
  ink: "#20344A",
  navy: "#315B7D",
  teal: "#21867A",
  green: "#237A57",
  greenLight: "#E2F3EA",
  amber: "#B46A17",
  amberLight: "#FFF0D8",
  red: "#A83A3A",
  redLight: "#FBE4E4",
  blueLight: "#E6F0F7",
  gray: "#D6DEE5",
  grayLight: "#F3F6F8",
  white: "#FFFFFF",
};

for (const sheet of [summary, costs, differences, details, calls, notes]) {
  sheet.showGridLines = false;
}

function title(sheet, address, text, subtitle = "") {
  sheet.getRange(address).merge();
  const anchor = sheet.getRange(address.split(":")[0]);
  anchor.values = [[text]];
  anchor.format = {
    fill: C.ink,
    font: { color: C.white, bold: true, size: 18 },
    verticalAlignment: "center",
  };
  sheet.getRange(address).format.rowHeight = 30;
  if (subtitle) {
    const startRow = Number(address.match(/\d+/)[0]) + 2;
    const endCol = address.split(":")[1].replace(/\d+/g, "");
    const subtitleRange = sheet.getRange(`A${startRow}:${endCol}${startRow}`);
    subtitleRange.merge();
    subtitleRange.values = [[subtitle]];
    subtitleRange.format = { fill: C.grayLight, font: { color: C.ink, italic: true }, wrapText: true };
  }
}

function header(range) {
  range.format = {
    fill: C.navy,
    font: { color: C.white, bold: true },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: C.gray },
  };
}

function body(range) {
  range.format = {
    wrapText: true,
    verticalAlignment: "top",
    borders: {
      insideHorizontal: { style: "thin", color: C.gray },
      bottom: { style: "thin", color: C.gray },
    },
  };
}

function addTable(sheet, address, name) {
  const table = sheet.tables.add(address, true, name);
  table.style = "TableStyleMedium2";
  table.showBandedRows = true;
  table.showFilterButton = true;
}

const metricKeys = [
  ["OTP Top-1 正確率", "otp_top1", "越高越好"],
  ["OTP Top-3 正確率", "otp_top3", "越高越好"],
  ["驗證連結擷取率", "verification_link", "越高越好"],
  ["負樣本誤抽率", "negative_false_extraction", "越低越好"],
];
const metricComparison = new Map(hybrid.comparison.map((row) => [row.metric, row]));
const comparisonName = {
  otp_top1: "OTP Top-1",
  otp_top3: "OTP Top-3",
  verification_link: "Verification Link",
  negative_false_extraction: "Negative False Extraction",
};

title(
  summary,
  "A1:N2",
  "Regex + OpenAI 實驗與外部成本比較",
  `128 筆資料 | 模型 ${regexOpenAI.experiment.openai.model} | 正式實驗 | 產生時間 ${new Date().toISOString().slice(0, 10)}`,
);

summary.getRange("A5:C5").merge();
summary.getRange("D5:F5").merge();
summary.getRange("G5:I5").merge();
summary.getRange("J5:L5").merge();
summary.getRange("A5").values = [["品質結論"]];
summary.getRange("D5").values = [["Regex+OpenAI API 呼叫"]];
summary.getRange("G5").values = [["Regex+OpenAI 總成本"]];
summary.getRange("J5").values = [["Hybrid 節省成本"]];
summary.getRange("A6:C7").merge();
summary.getRange("D6:F7").merge();
summary.getRange("G6:I7").merge();
summary.getRange("J6:L7").merge();
summary.getRange("A6").values = [["OTP 100% / 負樣本誤抽 0%"]];
summary.getRange("D6").values = [[regexOpenAI.api_usage.api_calls]];
summary.getRange("G6").values = [[regexOpenAI.api_usage.estimated_cost_usd]];
summary.getRange("J6").formulas = [["='\u5916\u90e8\u6210\u672c'!M12"]];
for (const range of ["A5:C5", "D5:F5", "G5:I5", "J5:L5"]) {
  summary.getRange(range).format = { fill: C.navy, font: { color: C.white, bold: true }, horizontalAlignment: "center" };
}
for (const range of ["A6:C7", "D6:F7", "G6:I7", "J6:L7"]) {
  summary.getRange(range).format = {
    fill: C.blueLight,
    font: { color: C.ink, bold: true, size: 15 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: C.gray },
  };
}
summary.getRange("G6").format.numberFormat = "$0.000000";
summary.getRange("J6").format.numberFormat = "0.0%";

summary.getRange("A10:N10").values = [[
  "指標", "方向",
  "Baseline 正確/誤抽數", "Baseline 樣本數", "Baseline 比率",
  "Regex+BGE 正確/誤抽數", "Regex+BGE 樣本數", "Regex+BGE 比率",
  "Hybrid 正確/誤抽數", "Hybrid 樣本數", "Hybrid 比率",
  "Regex+OpenAI 正確/誤抽數", "Regex+OpenAI 樣本數", "Regex+OpenAI 比率",
]];
const qualityRows = metricKeys.map(([label, key, direction]) => {
  const old = metricComparison.get(comparisonName[key]);
  const h = hybrid.metrics[key];
  const o = regexOpenAI.metrics[key];
  return [label, direction, old.baseline_hits, old.baseline_total, null, old.regex_v2_hits, old.regex_v2_total, null, h.hits, h.total, null, o.hits, o.total, null];
});
summary.getRange("A11:N14").values = qualityRows;
for (let row = 11; row <= 14; row += 1) {
  summary.getRange(`E${row}`).formulas = [[`=C${row}/D${row}`]];
  summary.getRange(`H${row}`).formulas = [[`=F${row}/G${row}`]];
  summary.getRange(`K${row}`).formulas = [[`=I${row}/J${row}`]];
  summary.getRange(`N${row}`).formulas = [[`=L${row}/M${row}`]];
}
header(summary.getRange("A10:N10"));
body(summary.getRange("A11:N14"));
summary.getRange("E11:E14").format.numberFormat = "0.0%";
summary.getRange("H11:H14").format.numberFormat = "0.0%";
summary.getRange("K11:K14").format.numberFormat = "0.0%";
summary.getRange("N11:N14").format.numberFormat = "0.0%";
summary.getRange("K11:N14").format.fill = C.greenLight;

summary.getRange("P10:T10").values = [["指標", "Baseline", "Regex+BGE", "Hybrid", "Regex+OpenAI"]];
summary.getRange("P11:P14").values = [["OTP Top-1 正確率"], ["OTP Top-3 正確率"], ["驗證連結擷取率"], ["負樣本正確拒絕率"]];
summary.getRange("Q11:T14").formulas = [
  ["=E11", "=H11", "=K11", "=N11"],
  ["=E12", "=H12", "=K12", "=N12"],
  ["=E13", "=H13", "=K13", "=N13"],
  ["=1-E14", "=1-H14", "=1-K14", "=1-N14"],
];
summary.getRange("Q11:T14").format.numberFormat = "0%";
const qualityChart = summary.charts.add("bar", summary.getRange("P10:T14"));
qualityChart.title = "品質比較（越高越好）";
qualityChart.hasLegend = true;
qualityChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
qualityChart.yAxis = { numberFormatCode: "0%", min: 0, max: 1 };
qualityChart.setPosition("P1", "X9");

summary.getRange("A17:L17").values = [[
  "架構", "API 呼叫", "覆蓋率", "總 tokens", "總成本 (USD)", "每千筆成本", "平均延遲 ms", "P50 ms", "P95 ms", "最大 ms", "API 延遲總和 s", "外傳訊息數",
]];
summary.getRange("A18:L20").values = [
  ["Regex+BGE", 0, 0, 0, 0, 0, null, null, null, null, 0, 0],
  ["Regex+BGE+OpenAI fallback", hybrid.api_usage.api_calls, hybrid.api_usage.api_calls / 128, hybrid.api_usage.total_tokens, hybrid.api_usage.estimated_cost_usd, hybrid.api_usage.estimated_cost_per_1000_messages_usd, hybrid.api_usage.average_latency_ms, hybridStats.median, hybridStats.p95, hybridStats.max, hybridStats.total / 1000, hybrid.api_usage.api_calls],
  ["Regex+OpenAI", regexOpenAI.api_usage.api_calls, regexOpenAI.api_usage.api_calls / 128, regexOpenAI.api_usage.total_tokens, regexOpenAI.api_usage.estimated_cost_usd, regexOpenAI.api_usage.estimated_cost_per_1000_messages_usd, regexOpenAI.api_usage.average_latency_ms, regexOpenAI.api_usage.median_latency_ms, regexOpenAI.api_usage.p95_latency_ms, regexOpenAI.api_usage.max_latency_ms, regexOpenAI.api_usage.api_latency_total_ms / 1000, regexOpenAI.api_usage.api_calls],
];
header(summary.getRange("A17:L17"));
body(summary.getRange("A18:L20"));
summary.getRange("C18:C20").format.numberFormat = "0.0%";
summary.getRange("E18:F20").format.numberFormat = "$0.000000";
summary.getRange("G18:K20").format.numberFormat = "#,##0.0";
summary.getRange("A20:L20").format.fill = C.amberLight;

summary.getRange("P17:R17").values = [["架構", "API 成本 (USD)", "API 呼叫數"]];
summary.getRange("P18:R20").formulas = [
  ["=A18", "=E18", "=B18"],
  ["=A19", "=E19", "=B19"],
  ["=A20", "=E20", "=B20"],
];
const costChart = summary.charts.add("bar", summary.getRange("P17:Q20"));
costChart.title = "API 外部成本 (USD)";
costChart.hasLegend = false;
costChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
costChart.yAxis = { numberFormatCode: "$0.000" };
costChart.setPosition("P21", "X35");

summary.getRange("A23:N23").merge();
summary.getRange("A23").values = [["實驗結論"]];
summary.getRange("A23:N23").format = { fill: C.teal, font: { color: C.white, bold: true } };
summary.getRange("A24:N27").merge();
summary.getRange("A24").values = [[
  "兩種 OpenAI 架構在本資料集的最終 Top-1 完全相同：OTP 103/103，負樣本誤抽 0/15。"
  + " Regex+OpenAI 的優點是流程簡單；但 Hybrid 只送 66 筆 API，相比 123 筆減少 57 次外傳與呼叫，"
  + "並且在品質不變下節省約 19.2% API 成本。因此這 128 筆上，Hybrid 是較合理的部署方案。",
]];
summary.getRange("A24:N27").format = { fill: C.greenLight, font: { color: C.ink, size: 12 }, wrapText: true, verticalAlignment: "center", borders: { preset: "outside", style: "thin", color: C.gray } };

summary.getRange("A:N").format.columnWidth = 13;
summary.getRange("A:A").format.columnWidth = 25;
summary.getRange("B:B").format.columnWidth = 14;
summary.getRange("P:P").format.columnWidth = 25;
summary.getRange("Q:T").format.columnWidth = 13;
summary.freezePanes.freezeRows(3);

title(costs, "A1:M2", "API 外部成本與延遲", "費率與 token 數據可稽核；缺少的延遲數據留白，不以 0 代替。");
costs.getRange("A5:D5").values = [["費率假設", "USD / 1M tokens", "模型", "來源"]];
costs.getRange("A6:D9").values = [
  ["未快取輸入", regexOpenAI.experiment.openai.input_usd_per_m, regexOpenAI.experiment.openai.model, "https://developers.openai.com/api/docs/models/gpt-5.6-luna"],
  ["快取輸入", regexOpenAI.experiment.openai.cached_input_usd_per_m, regexOpenAI.experiment.openai.model, "https://developers.openai.com/api/docs/models/gpt-5.6-luna"],
  ["Cache write", regexOpenAI.experiment.openai.cache_write_usd_per_m, regexOpenAI.experiment.openai.model, "https://developers.openai.com/api/docs/models/gpt-5.6-luna"],
  ["輸出", regexOpenAI.experiment.openai.output_usd_per_m, regexOpenAI.experiment.openai.model, "https://developers.openai.com/api/docs/models/gpt-5.6-luna"],
];
header(costs.getRange("A5:D5"));
body(costs.getRange("A6:D9"));
costs.getRange("B6:B9").format.numberFormat = "$0.00";

costs.getRange("A11:M11").values = [[
  "架構", "輸入 tokens", "快取 tokens", "Cache-write tokens", "未快取 tokens", "輸出 tokens", "公式重算成本", "JSON 記錄成本", "API 呼叫", "覆蓋率", "API 總延遲 s", "成本相對 Regex+OpenAI", "節省率相對 Regex+OpenAI",
]];
const hu = hybrid.api_usage;
const ou = regexOpenAI.api_usage;
costs.getRange("A12:M13").values = [
  ["Regex+BGE+OpenAI fallback", hu.input_tokens, hu.cached_input_tokens, hu.cache_write_tokens, null, hu.output_tokens, null, hu.estimated_cost_usd, hu.api_calls, hu.api_calls / 128, hybridStats.total / 1000, null, null],
  ["Regex+OpenAI", ou.input_tokens, ou.cached_input_tokens, ou.cache_write_tokens, null, ou.output_tokens, null, ou.estimated_cost_usd, ou.api_calls, ou.api_calls / 128, ou.api_latency_total_ms / 1000, null, null],
];
for (let row = 12; row <= 13; row += 1) {
  costs.getRange(`E${row}`).formulas = [[`=B${row}-C${row}-D${row}`]];
  costs.getRange(`G${row}`).formulas = [[`=(E${row}*$B$6+C${row}*$B$7+D${row}*$B$8+F${row}*$B$9)/1000000`]];
  costs.getRange(`L${row}`).formulas = [[`=H${row}/$H$13`]];
  costs.getRange(`M${row}`).formulas = [[`=1-L${row}`]];
}
header(costs.getRange("A11:M11"));
body(costs.getRange("A12:M13"));
costs.getRange("G12:H13").format.numberFormat = "$0.00000000";
costs.getRange("J12:J13").format.numberFormat = "0.0%";
costs.getRange("L12:M13").format.numberFormat = "0.0%";
costs.getRange("A12:M12").format.fill = C.greenLight;

costs.getRange("A16:F16").values = [["差異指標", "Hybrid", "Regex+OpenAI", "絕對差", "相對差", "解讀"]];
costs.getRange("A17:F22").values = [
  ["API 呼叫數", hu.api_calls, ou.api_calls, null, null, "Regex+OpenAI 對幾乎所有含候選訊息都呼叫 API"],
  ["外傳訊息覆蓋率", hu.api_calls / 128, ou.api_calls / 128, null, null, "覆蓋率也可視為資料外傳面積"],
  ["總 tokens", hu.total_tokens, ou.total_tokens, null, null, "Regex+OpenAI prompt 較短，因此 token 增幅小於呼叫數增幅"],
  ["總成本 (USD)", hu.estimated_cost_usd, ou.estimated_cost_usd, null, null, "兩者皆依 API 回報 token 與費率估算"],
  ["API 延遲總和 (s)", hybridStats.total / 1000, ou.api_latency_total_ms / 1000, null, null, "串行執行時的累計等待時間"],
  ["平均 API 延遲 (ms)", hu.average_latency_ms, ou.average_latency_ms, null, null, "單次延遲接近，總等待差異主要來自呼叫數"],
];
for (let row = 17; row <= 22; row += 1) {
  costs.getRange(`D${row}`).formulas = [[`=C${row}-B${row}`]];
  costs.getRange(`E${row}`).formulas = [[`=IF(B${row}=0,"",C${row}/B${row}-1)`]];
}
header(costs.getRange("A16:F16"));
body(costs.getRange("A17:F22"));
costs.getRange("B18:D18").format.numberFormat = "0.0%";
costs.getRange("B20:D20").format.numberFormat = "$0.000000";
costs.getRange("E17:E22").format.numberFormat = "+0.0%;-0.0%;0.0%";
costs.getRange("A17:F22").conditionalFormats.add("Custom", { formula: "=$D17>0", format: { fill: C.amberLight } });
costs.getRange("A:M").format.columnWidth = 15;
costs.getRange("A:A").format.columnWidth = 28;
costs.getRange("D:D").format.columnWidth = 54;
costs.getRange("F:F").format.columnWidth = 55;
costs.freezePanes.freezeRows(3);

title(differences, "A1:L2", "差異與遺漏", "列出兩種 OpenAI 架構不同、任一評估失敗或 API 錯誤的資料。");
differences.getRange("A5:L5").values = [[
  "序號", "ID", "來源", "類別", "Ground truth", "Hybrid Top-1", "Hybrid Top-3", "Regex+OpenAI Top-1", "Regex+OpenAI Top-3", "驗證連結", "錯誤類型", "說明",
]];
const notableValues = notableRows.map(({ hybrid: h, openai: o }) => {
  const issues = [];
  if (h.final_top3.join(",") !== o.final_top3.join(",")) issues.push("Top-3 不同");
  if (o.category === "link" && !o.link_correct) issues.push("驗證連結遺漏");
  if (o.category === "otp" && !o.top1_correct) issues.push("OTP Top-1 錯誤");
  if (o.category === "negative" && o.false_extraction) issues.push("負樣本誤抽");
  if (h.api_error || o.api_error) issues.push("API 錯誤");
  const explanation = h.final_top3.join(",") !== o.final_top3.join(",")
    ? "Regex+OpenAI 只保留真碼；Hybrid Top-3 另含日期/年份候選，Top-1 仍正確。"
    : "兩架構共用現有 URL 擷取結果，本次實驗未改寫連結模組。";
  return [o.sequence, o.dataset_id, o.source, o.category, shorten(o.ground_truth), h.final_top1, h.final_top3.join(", "), o.final_top1, o.final_top3.join(", "), shorten(o.final_url), issues.join("、"), explanation];
});
differences.getRangeByIndexes(5, 0, notableValues.length, 12).values = notableValues;
header(differences.getRange("A5:L5"));
body(differences.getRangeByIndexes(5, 0, notableValues.length, 12));
differences.getRangeByIndexes(5, 0, notableValues.length, 12).format.rowHeight = 42;
if (notableValues.length) addTable(differences, `A5:L${notableValues.length + 5}`, "NotableCasesTable");
differences.getRange("A:L").format.columnWidth = 14;
differences.getRange("E:E").format.columnWidth = 32;
differences.getRange("F:I").format.columnWidth = 22;
differences.getRange("J:J").format.columnWidth = 42;
differences.getRange("K:K").format.columnWidth = 24;
differences.getRange("L:L").format.columnWidth = 58;
differences.freezePanes.freezeRows(5);

title(details, "A1:T2", "128 筆全部明細", "無原始訊息內容；保留候選、路由、預測、正確性與成本欄位。");
details.getRange("A5:T5").values = [[
  "序號", "ID", "來源", "類別", "Ground truth", "Regex 候選", "BGE Top-3", "Hybrid 路由", "Hybrid Top-1", "Hybrid Top-3", "Regex+OpenAI 路由", "Regex+OpenAI Top-1", "Regex+OpenAI Top-3", "Top-1 相同", "OTP Top-1 正確", "OTP Top-3 正確", "URL 正確", "負樣本誤抽", "OpenAI 延遲 ms", "OpenAI 成本 USD",
]];
const detailValues = allRows.map(({ hybrid: h, openai: o }) => [
  o.sequence, o.dataset_id, o.source, o.category, shorten(o.ground_truth),
  o.candidates.map((candidate) => candidate.value).join(", "), h.bge_top3.join(", "), h.route,
  h.final_top1, h.final_top3.join(", "), o.route, o.final_top1, o.final_top3.join(", "),
  h.final_top1 === o.final_top1, Boolean(o.top1_correct), Boolean(o.top3_correct), Boolean(o.link_correct),
  Boolean(o.false_extraction), o.api?.latency_ms ?? null, o.api?.usage?.estimated_cost_usd ?? null,
]);
details.getRangeByIndexes(5, 0, detailValues.length, 20).values = detailValues;
header(details.getRange("A5:T5"));
body(details.getRangeByIndexes(5, 0, detailValues.length, 20));
details.getRangeByIndexes(5, 0, detailValues.length, 20).format.rowHeight = 22;
addTable(details, `A5:T${detailValues.length + 5}`, "AllResultsTable");
details.getRange("T6:T133").format.numberFormat = "$0.00000000";
details.getRange("A:T").format.columnWidth = 13;
details.getRange("E:G").format.columnWidth = 26;
details.getRange("H:M").format.columnWidth = 23;
details.freezePanes.freezeRows(5);
details.freezePanes.freezeColumns(2);

title(calls, "A1:N2", "API 呼叫明細", "Hybrid 與 Regex+OpenAI 的每次成功/失敗、token、成本與延遲。");
calls.getRange("A5:N5").values = [[
  "架構", "序號", "ID", "類別", "路由", "訊息類型", "原因碼", "選擇結果", "延遲 ms", "輸入 tokens", "快取 tokens", "Cache-write tokens", "輸出 tokens", "成本 USD",
]];
const callValues = apiRows.map(({ architecture, row }) => [
  architecture, row.sequence, row.dataset_id, row.category, row.route,
  row.api?.message_type ?? "API_ERROR", row.api?.reason_code ?? row.api_error,
  row.final_top1 || "NO_OTP", row.api?.latency_ms ?? null, row.api?.usage?.input_tokens ?? null,
  row.api?.usage?.cached_input_tokens ?? null, row.api?.usage?.cache_write_tokens ?? null,
  row.api?.usage?.output_tokens ?? null, row.api?.usage?.estimated_cost_usd ?? null,
]);
calls.getRangeByIndexes(5, 0, callValues.length, 14).values = callValues;
header(calls.getRange("A5:N5"));
body(calls.getRangeByIndexes(5, 0, callValues.length, 14));
addTable(calls, `A5:N${callValues.length + 5}`, "APICallsTable");
calls.getRange(`N6:N${callValues.length + 5}`).format.numberFormat = "$0.00000000";
calls.getRange("A:N").format.columnWidth = 15;
calls.getRange("A:A").format.columnWidth = 31;
calls.getRange("E:G").format.columnWidth = 27;
calls.freezePanes.freezeRows(5);
calls.freezePanes.freezeColumns(3);

title(notes, "A1:F2", "定義、局限與資料來源", "本頁讓實驗結果可重現、可解釋，並清楚界定沒有量到的東西。");
notes.getRange("A5:F5").values = [["主題", "定義/說明", "數值", "單位", "資料來源", "備註"]];
notes.getRange("A6:F15").values = [
  ["資料集", "OTP 正樣本 103、驗證連結 10、負樣本 15", 128, "筆", "otp_dataset_dedup.xlsx", "兩架構使用同一份資料"],
  ["Regex+OpenAI 路由", "只要 Regex v2 有一個以上 OTP 候選就呼叫 API；無候選直接 NO_OTP", 123, "API 呼叫", "regex_openai_experiment_results.json", "不使用 BGE 分數"],
  ["Hybrid 路由", "Regex v2 候選先經 BGE gate，只將模糊案例送 API", 66, "API 呼叫", "bge_openai_experiment_results.json", "62 筆 BGE 直接輸出"],
  ["外部成本", "依 Responses API 回報 token 使用量與 .env 保存費率計算", regexOpenAI.api_usage.estimated_cost_usd, "USD", "https://developers.openai.com/api/docs/models/gpt-5.6-luna", "未包含網路、人力與電力"],
  ["API 延遲", "從發出 API 要求到取得並解析結構化回應的 wall-clock 時間", regexOpenAI.api_usage.average_latency_ms, "ms/次", "regex_openai_experiment_results.json", "串行執行；實際服務可以並行"],
  ["能耗", "本次依使用者決定不量測", null, null, null, "OpenAI API 也不回傳單次推論能耗"],
  ["驗證連結", "兩個新實驗沿用原 Regex v2+BGE URL prediction，未將 URL 改送 OpenAI", 7 / 10, "正確率", "regex_v2_experiment_results.json", "因此兩架構都是 7/10"],
  ["評估泄漏防護", "Ground truth 只在完成預測後用於計分，不送入 prompt 或路由", true, null, "run_regex_openai_experiment.py", "Prompt 另存 SHA-256 於 JSON"],
  ["快取計價", "Cache-write token 使用 .env 費率 0.25 USD / 1M", regexOpenAI.experiment.openai.cache_write_usd_per_m, "USD / 1M", "https://developers.openai.com/api/docs/models/gpt-5.6-luna", "保留 API 實際回報 token 類別"],
  ["最終建議", "本資料集上 Hybrid 與 Regex+OpenAI 品質相同，但 Hybrid 外部呼叫、資料外傳與成本較低", null, null, null, "上線前應用新的 holdout set 再驗證"],
];
header(notes.getRange("A5:F5"));
body(notes.getRange("A6:F15"));
notes.getRange("C9").format.numberFormat = "$0.000000";
notes.getRange("C12").format.numberFormat = "0.0%";
notes.getRange("C14").format.numberFormat = "$0.00";
notes.getRange("A:F").format.columnWidth = 18;
notes.getRange("A:A").format.columnWidth = 24;
notes.getRange("B:B").format.columnWidth = 72;
notes.getRange("E:E").format.columnWidth = 55;
notes.getRange("F:F").format.columnWidth = 48;
notes.freezePanes.freezeRows(5);

for (const sheet of [summary, costs, differences, details, calls, notes]) {
  const used = sheet.getUsedRange();
  used.format.font = { name: "Aptos", size: 10, color: C.ink };
}

// Reapply title/header font colors after the workbook-wide body font baseline.
for (const [sheet, titleRange, headerRanges] of [
  [summary, "A1:N2", ["A10:N10", "A17:L17"]],
  [costs, "A1:M2", ["A5:D5", "A11:M11", "A16:F16"]],
  [differences, "A1:L2", ["A5:L5"]],
  [details, "A1:T2", ["A5:T5"]],
  [calls, "A1:N2", ["A5:N5"]],
  [notes, "A1:F2", ["A5:F5"]],
]) {
  sheet.getRange(titleRange).format.font = { name: "Aptos Display", size: 18, bold: true, color: C.white };
  for (const range of headerRanges) header(sheet.getRange(range));
}

const summaryInspect = await wb.inspect({
  kind: "table",
  range: "實驗摘要!A1:N27",
  include: "values,formulas",
  tableMaxRows: 30,
  tableMaxCols: 14,
});
console.log(summaryInspect.ndjson);
const errors = await wb.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

for (const sheetName of ["實驗摘要", "外部成本", "差異與遺漏", "全部明細", "API 呼叫明細", "定義與來源"]) {
  const preview = await wb.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, notableRows: notableRows.length, apiRows: apiRows.length }));
process.exitCode = 0;
