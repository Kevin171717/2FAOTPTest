import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const DATA_PATH = path.join(ROOT, "results", "regex_v2", "regex_v2_report_data.json");
const OUTPUT_DIR = path.join(ROOT, "outputs", "regex_v2_recall_report");
const OUTPUT_PATH = path.join(OUTPUT_DIR, "regex_v2_recall_report.xlsx");
const PREVIEW_DIR = "C:/Users/Owner/AppData/Local/Temp/codex_regex_v2_report_previews";

const data = JSON.parse(await fs.readFile(DATA_PATH, "utf8"));
await fs.mkdir(OUTPUT_DIR, { recursive: true });
await fs.mkdir(PREVIEW_DIR, { recursive: true });

const wb = Workbook.create();
const summary = wb.worksheets.add("Summary");
const recovered = wb.worksheets.add("Recovered Cases");
const misses = wb.worksheets.add("Final Misses");
const otpDetail = wb.worksheets.add("OTP Detail");
const negativeDetail = wb.worksheets.add("Negative Detail");
const recommendations = wb.worksheets.add("Recommendations");

const COLORS = {
  ink: "#17324D",
  teal: "#0F766E",
  tealLight: "#CCFBF1",
  green: "#15803D",
  greenLight: "#DCFCE7",
  orange: "#C2410C",
  orangeLight: "#FFEDD5",
  red: "#B91C1C",
  redLight: "#FEE2E2",
  blue: "#1D4ED8",
  blueLight: "#DBEAFE",
  gray50: "#F8FAFC",
  gray100: "#F1F5F9",
  gray300: "#CBD5E1",
  gray600: "#475569",
  white: "#FFFFFF",
};

function titleBand(sheet, range, title) {
  sheet.getRange(range).merge();
  const cell = sheet.getRange(range.split(":")[0]);
  cell.values = [[title]];
  cell.format = {
    fill: COLORS.ink,
    font: { color: COLORS.white, bold: true, size: 18 },
    verticalAlignment: "center",
    horizontalAlignment: "left",
  };
  sheet.getRange(range).format.rowHeight = 28;
}

function sectionHeader(range) {
  range.format = {
    fill: COLORS.teal,
    font: { color: COLORS.white, bold: true },
    borders: { preset: "all", style: "thin", color: COLORS.gray300 },
    verticalAlignment: "center",
    wrapText: true,
  };
  range.format.rowHeight = 24;
}

function bodyGrid(range) {
  range.format = {
    borders: { preset: "all", style: "thin", color: COLORS.gray300 },
    verticalAlignment: "top",
    wrapText: true,
  };
}

function addTable(sheet, address, name) {
  const table = sheet.tables.add(address, true, name);
  table.style = "TableStyleMedium2";
  table.showBandedRows = true;
  table.showFilterButton = true;
  return table;
}

for (const sheet of [summary, recovered, misses, otpDetail, negativeDetail, recommendations]) {
  sheet.showGridLines = false;
}

// OTP detail is written before the Summary formulas that reference it.
const otpHeaders = [
  "Dataset ID", "來源", "正確 OTP", "使用主旨代理", "v1 正文命中", "v1 正文+主旨命中",
  "v2 正文命中", "v2 正文+主旨命中", "v1 候選數", "v2 候選數", "新增候選數",
  "v2 規則類型", "v2 候選", "Baseline Top-1", "Baseline Top-1 命中",
  "Baseline Top-3", "Baseline Top-3 命中", "v2 Top-1", "v2 Top-1 命中",
  "v2 Top-3", "v2 Top-3 命中",
];
const otpRows = data.otp_detail.map((row) => [
  row.dataset_id, row.source, row.ground_truth, row.subject_proxy_used,
  row.v1_body_hit, row.v1_combined_hit, row.v2_body_hit, row.v2_combined_hit,
  row.v1_candidate_count, row.v2_candidate_count, row.v2_extra_candidate_count,
  row.v2_patterns, row.v2_candidates, row.baseline_top1, row.baseline_top1_hit,
  row.baseline_top3, row.baseline_top3_hit, row.v2_top1, row.v2_top1_hit,
  row.v2_top3, row.v2_top3_hit,
]);
otpDetail.getRangeByIndexes(0, 0, otpRows.length + 1, otpHeaders.length).values = [otpHeaders, ...otpRows];
addTable(otpDetail, `A1:U${otpRows.length + 1}`, "OtpDetailTable");
sectionHeader(otpDetail.getRange("A1:U1"));
bodyGrid(otpDetail.getRange(`A2:U${otpRows.length + 1}`));
otpDetail.freezePanes.freezeRows(1);
otpDetail.freezePanes.freezeColumns(3);
otpDetail.getRange("A:A").format.columnWidth = 11;
otpDetail.getRange("B:B").format.columnWidth = 10;
otpDetail.getRange("C:C").format.columnWidth = 15;
otpDetail.getRange("D:K").format.columnWidth = 13;
otpDetail.getRange("L:M").format.columnWidth = 30;
otpDetail.getRange("N:U").format.columnWidth = 20;
otpDetail.getRange(`D2:K${otpRows.length + 1}`).format.horizontalAlignment = "center";
otpDetail.getRange(`O2:O${otpRows.length + 1}`).format.horizontalAlignment = "center";
otpDetail.getRange(`Q2:Q${otpRows.length + 1}`).format.horizontalAlignment = "center";
otpDetail.getRange(`S2:S${otpRows.length + 1}`).format.horizontalAlignment = "center";
otpDetail.getRange(`U2:U${otpRows.length + 1}`).format.horizontalAlignment = "center";
for (const col of ["E", "F", "G", "H", "O", "Q", "S", "U"]) {
  const hitRange = otpDetail.getRange(`${col}2:${col}${otpRows.length + 1}`);
  hitRange.conditionalFormats.add("cellIs", {
    operator: "equal", formula: 1, format: { fill: COLORS.greenLight, font: { color: COLORS.green, bold: true } },
  });
  hitRange.conditionalFormats.add("cellIs", {
    operator: "equal", formula: 0, format: { fill: COLORS.redLight, font: { color: COLORS.red, bold: true } },
  });
}

// Negative examples support the false-extraction formulas on Summary.
const negativeHeaders = ["Dataset ID", "Baseline 誤抽", "Regex v2 誤抽", "v2 Top-3 輸出"];
const negativeRows = data.negative_detail.map((row) => [
  row.dataset_id, row.baseline_false_extraction, row.v2_false_extraction, row.v2_top3,
]);
negativeDetail.getRangeByIndexes(0, 0, negativeRows.length + 1, negativeHeaders.length).values = [negativeHeaders, ...negativeRows];
addTable(negativeDetail, `A1:D${negativeRows.length + 1}`, "NegativeDetailTable");
sectionHeader(negativeDetail.getRange("A1:D1"));
bodyGrid(negativeDetail.getRange(`A2:D${negativeRows.length + 1}`));
negativeDetail.freezePanes.freezeRows(1);
negativeDetail.getRange("A:A").format.columnWidth = 12;
negativeDetail.getRange("B:C").format.columnWidth = 18;
negativeDetail.getRange("D:D").format.columnWidth = 45;
for (const col of ["B", "C"]) {
  const range = negativeDetail.getRange(`${col}2:${col}${negativeRows.length + 1}`);
  range.conditionalFormats.add("cellIs", {
    operator: "equal", formula: 1, format: { fill: COLORS.redLight, font: { color: COLORS.red, bold: true } },
  });
  range.conditionalFormats.add("cellIs", {
    operator: "equal", formula: 0, format: { fill: COLORS.greenLight, font: { color: COLORS.green } },
  });
}

// Summary dashboard.
titleBand(summary, "A1:N2", "Regex v2 OTP 候選召回率與最終抽取實驗");
summary.getRange("A3:N3").merge();
summary.getRange("A3").values = [[
  `資料集 ${data.metadata.dataset_rows} 筆：OTP ${data.metadata.otp_rows}、驗證連結 ${data.metadata.link_rows}、負樣本 ${data.metadata.negative_rows}。模型：${data.metadata.model}`,
]];
summary.getRange("A3:N3").format = {
  fill: COLORS.gray100, font: { color: COLORS.gray600, italic: true }, wrapText: true,
};
summary.getRange("A4:N4").merge();
summary.getRange("A4").values = [[data.metadata.subject_proxy_note]];
summary.getRange("A4:N4").format = {
  fill: COLORS.orangeLight, font: { color: COLORS.orange, bold: true }, wrapText: true,
};

summary.getRange("A6:E6").values = [["Regex 版本／欄位", "命中", "OTP 總數", "Recall", "說明"]];
sectionHeader(summary.getRange("A6:E6"));
summary.getRange("A7:A10").values = data.recall_scenarios.map((row) => [row.scenario]);
summary.getRange("E7:E10").values = data.recall_scenarios.map((row) => [row.scope]);
summary.getRange("B7").formulas = [[`=SUM('OTP Detail'!E2:E${otpRows.length + 1})`]];
summary.getRange("B8").formulas = [[`=SUM('OTP Detail'!F2:F${otpRows.length + 1})`]];
summary.getRange("B9").formulas = [[`=SUM('OTP Detail'!G2:G${otpRows.length + 1})`]];
summary.getRange("B10").formulas = [[`=SUM('OTP Detail'!H2:H${otpRows.length + 1})`]];
summary.getRange("C7:C10").formulas = data.recall_scenarios.map(() => [[`=COUNTA('OTP Detail'!A2:A${otpRows.length + 1})`]][0]);
summary.getRange("D7").formulas = [["=B7/C7"]];
summary.getRange("D7:D10").fillDown();
bodyGrid(summary.getRange("A7:E10"));
summary.getRange("D7:D10").format.numberFormat = "0.0%";
summary.getRange("B7:D10").format.horizontalAlignment = "center";
summary.getRange("D10").format = {
  fill: COLORS.greenLight, font: { color: COLORS.green, bold: true, size: 12 },
  numberFormat: "0.0%", borders: { preset: "all", style: "medium", color: COLORS.green },
};

summary.getRange("M6:N6").values = [["Regex 情境", "Recall"]];
summary.getRange("M7:M10").formulas = [["=A7"], ["=A8"], ["=A9"], ["=A10"]];
summary.getRange("N7:N10").formulas = [["=D7"], ["=D8"], ["=D9"], ["=D10"]];
summary.getRange("N7:N10").format.numberFormat = "0.0%";
const recallChart = summary.charts.add("bar", summary.getRange("M6:N10"));
recallChart.title = "Regex 候選 Recall 比較";
recallChart.hasLegend = false;
recallChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
recallChart.yAxis = { numberFormatCode: "0.0%", min: 0.9, max: 1.0 };
recallChart.setPosition("G20", "N32");

summary.getRange("A13:J13").values = [[
  "最終指標", "方向", "Baseline 命中", "Baseline 總數", "Baseline 比率",
  "Regex v2 命中", "Regex v2 總數", "Regex v2 比率", "命中差", "百分點差",
]];
sectionHeader(summary.getRange("A13:J13"));
summary.getRange("A14:B17").values = data.final_metrics.map((row) => [row.metric, row.direction]);
summary.getRange("C14").formulas = [[`=SUM('OTP Detail'!O2:O${otpRows.length + 1})`]];
summary.getRange("D14").formulas = [[`=COUNTA('OTP Detail'!A2:A${otpRows.length + 1})`]];
summary.getRange("F14").formulas = [[`=SUM('OTP Detail'!S2:S${otpRows.length + 1})`]];
summary.getRange("G14").formulas = [[`=COUNTA('OTP Detail'!A2:A${otpRows.length + 1})`]];
summary.getRange("C15").formulas = [[`=SUM('OTP Detail'!Q2:Q${otpRows.length + 1})`]];
summary.getRange("D15").formulas = [[`=COUNTA('OTP Detail'!A2:A${otpRows.length + 1})`]];
summary.getRange("F15").formulas = [[`=SUM('OTP Detail'!U2:U${otpRows.length + 1})`]];
summary.getRange("G15").formulas = [[`=COUNTA('OTP Detail'!A2:A${otpRows.length + 1})`]];
summary.getRange("C16:D16").values = [[data.final_metrics[2].baseline_hits, data.final_metrics[2].baseline_total]];
summary.getRange("F16:G16").values = [[data.final_metrics[2].v2_hits, data.final_metrics[2].v2_total]];
summary.getRange("C17").formulas = [[`=SUM('Negative Detail'!B2:B${negativeRows.length + 1})`]];
summary.getRange("D17").formulas = [[`=COUNTA('Negative Detail'!A2:A${negativeRows.length + 1})`]];
summary.getRange("F17").formulas = [[`=SUM('Negative Detail'!C2:C${negativeRows.length + 1})`]];
summary.getRange("G17").formulas = [[`=COUNTA('Negative Detail'!A2:A${negativeRows.length + 1})`]];
summary.getRange("E14").formulas = [["=C14/D14"]];
summary.getRange("E14:E17").fillDown();
summary.getRange("H14").formulas = [["=F14/G14"]];
summary.getRange("H14:H17").fillDown();
summary.getRange("I14").formulas = [["=F14-C14"]];
summary.getRange("I14:I17").fillDown();
summary.getRange("J14").formulas = [["=H14-E14"]];
summary.getRange("J14:J17").fillDown();
bodyGrid(summary.getRange("A14:J17"));
summary.getRange("E14:E17").format.numberFormat = "0.0%";
summary.getRange("H14:H17").format.numberFormat = "0.0%";
summary.getRange("J14:J17").format.numberFormat = "+0.0%;-0.0%;0.0%";
summary.getRange("C14:J17").format.horizontalAlignment = "center";
summary.getRange("H14:H16").format.fill = COLORS.greenLight;
summary.getRange("H17").format.fill = COLORS.redLight;

summary.getRange("A20:D20").values = [["候選噪音診斷", "Baseline", "Regex v2", "變化"]];
sectionHeader(summary.getRange("A20:D20"));
summary.getRange("A21:A22").values = [["每封 OTP 平均唯一候選數"], ["負樣本誤抽筆數"]];
summary.getRange("B21").formulas = [[`=AVERAGE('OTP Detail'!I2:I${otpRows.length + 1})`]];
summary.getRange("C21").formulas = [[`=AVERAGE('OTP Detail'!J2:J${otpRows.length + 1})`]];
summary.getRange("D21").formulas = [["=C21-B21"]];
summary.getRange("B22").formulas = [[`=SUM('Negative Detail'!B2:B${negativeRows.length + 1})`]];
summary.getRange("C22").formulas = [[`=SUM('Negative Detail'!C2:C${negativeRows.length + 1})`]];
summary.getRange("D22").formulas = [["=C22-B22"]];
bodyGrid(summary.getRange("A21:D22"));
summary.getRange("B21:D21").format.numberFormat = "0.00";
summary.getRange("B21:D22").format.horizontalAlignment = "center";

summary.getRange("A34:N36").merge();
summary.getRange("A34").values = [[
  "結論：Regex v2 在正文＋主旨代理時達到 103/103（100%）候選 recall，且 Top-1 增加 3 筆、Top-3 增加 6 筆；但候選範圍放寬使負樣本誤抽增加 2 筆。下一步應加上局部 OTP 語境閘門與 hard-negative 排除，再優化 BGE/通知分類門檻。",
]];
summary.getRange("A34:N36").format = {
  fill: COLORS.blueLight, font: { color: COLORS.ink, bold: true, size: 11 },
  borders: { preset: "outside", style: "medium", color: COLORS.blue },
  verticalAlignment: "center", wrapText: true,
};
summary.freezePanes.freezeRows(4);
summary.getRange("A:A").format.columnWidth = 27;
summary.getRange("B:D").format.columnWidth = 13;
summary.getRange("E:E").format.columnWidth = 26;
summary.getRange("F:J").format.columnWidth = 13;
summary.getRange("K:L").format.columnWidth = 4;
summary.getRange("M:M").format.columnWidth = 26;
summary.getRange("N:N").format.columnWidth = 12;

// Six Regex v1 misses recovered by v2 and/or subject.
titleBand(recovered, "A1:J2", "六個 Regex v1 漏失案例的修復結果");
const recoveredHeaders = [
  "Dataset ID", "來源", "正確 OTP", "v1 漏失原因", "v2 新規則", "v2 正文命中",
  "需要主旨", "v2 Top-1", "Top-1 命中", "v2 Top-3",
];
const recoveredRows = data.recovered_cases.map((row) => [
  row.dataset_id, row.source, row.ground_truth, row.v1_miss_reason, row.v2_rule,
  row.v2_body_hit, row.subject_needed, row.v2_top1, row.top1_hit, row.v2_top3,
]);
recovered.getRangeByIndexes(3, 0, recoveredRows.length + 1, recoveredHeaders.length).values = [recoveredHeaders, ...recoveredRows];
addTable(recovered, `A4:J${recoveredRows.length + 4}`, "RecoveredCasesTable");
sectionHeader(recovered.getRange("A4:J4"));
bodyGrid(recovered.getRange(`A5:J${recoveredRows.length + 4}`));
recovered.freezePanes.freezeRows(4);
recovered.getRange("A:C").format.columnWidth = 13;
recovered.getRange("D:D").format.columnWidth = 44;
recovered.getRange("E:G").format.columnWidth = 18;
recovered.getRange("H:J").format.columnWidth = 24;
recovered.getRange(`F5:I${recoveredRows.length + 4}`).format.horizontalAlignment = "center";
recovered.getRange(`I5:I${recoveredRows.length + 4}`).conditionalFormats.add("containsText", {
  text: "是", format: { fill: COLORS.greenLight, font: { color: COLORS.green, bold: true } },
});

// Final Top-3 misses after Regex v2.
titleBand(misses, "A1:H2", "Regex v2 後仍未進入最終 Top-3 的 OTP");
misses.getRange("A3:H3").merge();
misses.getRange("A3").values = [[
  "這些案例的正確 OTP 都已被 Regex v2 找到；失敗點位於通知分類、anchor 門檻或後續排序，而非候選 recall。",
]];
misses.getRange("A3:H3").format = { fill: COLORS.orangeLight, font: { color: COLORS.orange, bold: true }, wrapText: true };
const missHeaders = ["Dataset ID", "來源", "正確 OTP", "v2 Top-1", "v2 Top-3", "候選已找到", "候選數", "診斷"];
const missRows = data.final_misses.map((row) => [
  row.dataset_id, row.source, row.ground_truth, row.v2_top1, row.v2_top3,
  row.candidate_found, row.candidate_count, row.diagnosis,
]);
misses.getRangeByIndexes(4, 0, missRows.length + 1, missHeaders.length).values = [missHeaders, ...missRows];
addTable(misses, `A5:H${missRows.length + 5}`, "FinalMissesTable");
sectionHeader(misses.getRange("A5:H5"));
bodyGrid(misses.getRange(`A6:H${missRows.length + 5}`));
misses.freezePanes.freezeRows(5);
misses.getRange("A:C").format.columnWidth = 14;
misses.getRange("D:E").format.columnWidth = 24;
misses.getRange("F:G").format.columnWidth = 14;
misses.getRange("H:H").format.columnWidth = 48;
misses.getRange(`F6:F${missRows.length + 5}`).format.fill = COLORS.greenLight;

// Prioritized next steps.
titleBand(recommendations, "A1:E2", "提升最終正確率的建議優先級");
const recommendationHeaders = ["優先級", "建議動作", "目前問題", "預期效果", "驗證方式"];
const recommendationRows = data.recommendations.map((row) => [
  row.priority, row.action, row.why, row.expected, row.validation,
]);
recommendations.getRangeByIndexes(3, 0, recommendationRows.length + 1, recommendationHeaders.length).values = [recommendationHeaders, ...recommendationRows];
addTable(recommendations, `A4:E${recommendationRows.length + 4}`, "RecommendationsTable");
sectionHeader(recommendations.getRange("A4:E4"));
bodyGrid(recommendations.getRange(`A5:E${recommendationRows.length + 4}`));
recommendations.freezePanes.freezeRows(4);
recommendations.getRange("A:A").format.columnWidth = 10;
recommendations.getRange("B:B").format.columnWidth = 42;
recommendations.getRange("C:E").format.columnWidth = 48;
recommendations.getRange(`A5:A${recommendationRows.length + 4}`).format = {
  fill: COLORS.orangeLight, font: { color: COLORS.orange, bold: true }, horizontalAlignment: "center",
  borders: { preset: "all", style: "thin", color: COLORS.gray300 },
};

const previewSpecs = [
  ["Summary", "A1:N36", "summary.png"],
  ["Recovered Cases", "A1:J11", "recovered_cases.png"],
  ["Final Misses", "A1:H14", "final_misses.png"],
  ["OTP Detail", "A1:U24", "otp_detail.png"],
  ["Negative Detail", "A1:D16", "negative_detail.png"],
  ["Recommendations", "A1:E11", "recommendations.png"],
];
for (const [sheetName, range, filename] of previewSpecs) {
  const preview = await wb.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(PREVIEW_DIR, filename), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(OUTPUT_PATH);
console.log(`Workbook: ${OUTPUT_PATH}`);
console.log(`Previews: ${PREVIEW_DIR}`);
