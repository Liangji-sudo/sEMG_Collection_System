import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const workDir = path.dirname(fileURLToPath(import.meta.url));
const outputDir = path.resolve(workDir, "../data_collection_progress");
const outputPath = path.join(outputDir, "数据采集人员任务进度表_v10_每页20人_编号任务.xlsx");

function groupNo(n) {
  return Math.ceil(n / 100);
}

function c2Label(n) {
  return `C2-${groupNo(n)}`;
}

function c3Label(n) {
  return `C3-${groupNo(n)}`;
}

function subjectRows(startSubject, endSubject) {
  const rows = [];
  for (let i = startSubject; i <= endSubject; i += 1) {
    const configs = [
      [`L${String(i).padStart(3, "0")}`, "C1"],
      [`L${String(i).padStart(3, "0")}`, c2Label(i)],
      [`L${String(i).padStart(3, "0")}`, c3Label(i)],
      [`L${String(i).padStart(3, "0")}`, "C4"],
      [`D${String(i).padStart(3, "0")}`, "连续手势1"],
      [`D${String(i).padStart(3, "0")}`, "连续手势2"],
    ];

    configs.forEach(([task, subTask], idx) => {
      rows.push([
        idx === 0 ? i : null,
        idx === 0 || idx === 4 ? task : null,
        subTask,
        "",
        "",
        null,
      ]);
    });
  }
  return rows;
}

function applyBlockStyle(sheet, range, firstCol, lastCol, dateCol, startRow, rowCount) {
  const endRow = startRow + rowCount - 1;
  sheet.getRange(range).format = {
    verticalAlignment: "center",
    horizontalAlignment: "center",
    borders: { preset: "inside", style: "thin", color: "#E5E7EB" },
  };
  sheet.getRange(`${firstCol}${startRow}:${firstCol}${endRow}`).format.numberFormat = "000";
  sheet.getRange(`${dateCol}${startRow}:${dateCol}${endRow}`).format.numberFormat = "yyyy-mm-dd";

  sheet.getRange(`${firstCol}${startRow}:${String.fromCharCode(firstCol.charCodeAt(0) + 1)}${endRow}`).format = {
    horizontalAlignment: "center",
    verticalAlignment: "center",
    font: { bold: true, color: "#000000" },
  };
  sheet.getRange(`${firstCol}${startRow}:${String.fromCharCode(firstCol.charCodeAt(0) + 1)}${endRow}`).format.borders = {
    preset: "all",
    style: "thin",
    color: "#D1D5DB",
  };
}

function mergeSubjectBlocks(sheet, startCol, endCol, firstDataRow, subjectCount) {
  const taskCol = String.fromCharCode(startCol.charCodeAt(0) + 1);
  for (let i = 0; i < subjectCount; i += 1) {
    const startRow = firstDataRow + i * 6;
    sheet.getRange(`${startCol}${startRow}:${startCol}${startRow + 5}`).merge();
    sheet.getRange(`${taskCol}${startRow}:${taskCol}${startRow + 3}`).merge();
    sheet.getRange(`${taskCol}${startRow + 4}:${taskCol}${startRow + 5}`).merge();
    if (i % 2 === 0) {
      sheet.getRange(`${startCol}${startRow}:${endCol}${startRow + 5}`).format.fill = "#F1FAFE";
    }
  }
}

const workbook = Workbook.create();
const main = workbook.worksheets.add("任务进度表");
const roster = workbook.worksheets.add("受试者清单");
const notes = workbook.worksheets.add("说明");

main.showGridLines = false;
roster.showGridLines = false;
notes.showGridLines = false;

const headers = [["编号", "一级", "二级任务", "完成", "工作人员", "日期"]];
const subjectsPerColumn = 10;
const subjectsPerPage = subjectsPerColumn * 2;
const rowsPerColumn = subjectsPerColumn * 6;
const pageBlockRows = 64;
const pageCount = 20;

for (let page = 0; page < pageCount; page += 1) {
  const pageStartRow = 1 + page * pageBlockRows;
  const headerRow = pageStartRow + 1;
  const dataStartRow = pageStartRow + 2;
  const firstSubject = page * subjectsPerPage + 1;
  const leftStart = firstSubject;
  const leftEnd = firstSubject + subjectsPerColumn - 1;
  const rightStart = firstSubject + subjectsPerColumn;
  const rightEnd = firstSubject + subjectsPerPage - 1;

  main.getRange(`A${pageStartRow}:M${pageStartRow}`).merge();
  main.getRange(`A${pageStartRow}`).values = [[
    `数据采集人员任务进度表 v10（每页20人）  第 ${page + 1} 页：${String(firstSubject).padStart(3, "0")}-${String(rightEnd).padStart(3, "0")}`,
  ]];
  main.getRange(`A${pageStartRow}`).format = {
    fill: "#1F4E79",
    font: { bold: true, color: "#FFFFFF", size: 14 },
    horizontalAlignment: "center",
  };
  main.getRange(`A${pageStartRow}:M${pageStartRow}`).format.rowHeight = 24;

  main.getRange(`A${headerRow}:F${headerRow}`).values = headers;
  main.getRange(`H${headerRow}:M${headerRow}`).values = headers;
  for (const headerRange of [`A${headerRow}:F${headerRow}`, `H${headerRow}:M${headerRow}`]) {
    main.getRange(headerRange).format = {
      fill: "#D9EAF7",
      font: { bold: true, color: "#17365D" },
      horizontalAlignment: "center",
      verticalAlignment: "center",
    };
  }

  const leftRows = subjectRows(leftStart, leftEnd);
  const rightRows = subjectRows(rightStart, rightEnd);
  main.getRangeByIndexes(dataStartRow - 1, 0, rowsPerColumn, 6).values = leftRows;
  main.getRangeByIndexes(dataStartRow - 1, 7, rowsPerColumn, 6).values = rightRows;

  applyBlockStyle(main, `A${headerRow}:F${dataStartRow + rowsPerColumn - 1}`, "A", "F", "F", dataStartRow, rowsPerColumn);
  applyBlockStyle(main, `H${headerRow}:M${dataStartRow + rowsPerColumn - 1}`, "H", "M", "M", dataStartRow, rowsPerColumn);
  mergeSubjectBlocks(main, "A", "F", dataStartRow, subjectsPerColumn);
  mergeSubjectBlocks(main, "H", "M", dataStartRow, subjectsPerColumn);

  for (const statusCol of ["D", "K"]) {
    main.getRange(`${statusCol}${dataStartRow}:${statusCol}${dataStartRow + rowsPerColumn - 1}`).conditionalFormats.add("containsText", {
      text: "已完成",
      format: { fill: "#DCFCE7", font: { color: "#166534" } },
    });
    main.getRange(`${statusCol}${dataStartRow}:${statusCol}${dataStartRow + rowsPerColumn - 1}`).conditionalFormats.add("containsText", {
      text: "进行中",
      format: { fill: "#FEF3C7", font: { color: "#92400E" } },
    });
    main.getRange(`${statusCol}${dataStartRow}:${statusCol}${dataStartRow + rowsPerColumn - 1}`).conditionalFormats.add("containsText", {
      text: "需复采",
      format: { fill: "#FEE2E2", font: { color: "#991B1B" } },
    });
  }
}

for (const col of ["A", "H"]) main.getRange(`${col}:${col}`).format.columnWidth = 11;
for (const col of ["B", "I"]) main.getRange(`${col}:${col}`).format.columnWidth = 10;
for (const col of ["C", "J"]) main.getRange(`${col}:${col}`).format.columnWidth = 15;
for (const col of ["D", "K"]) main.getRange(`${col}:${col}`).format.columnWidth = 11;
for (const col of ["E", "L"]) main.getRange(`${col}:${col}`).format.columnWidth = 12;
for (const col of ["F", "M"]) main.getRange(`${col}:${col}`).format.columnWidth = 12;
main.getRange("G:G").format.columnWidth = 3;
main.freezePanes.freezeRows(2);

roster.getRange("A1:H1").merge();
roster.getRange("A1").values = [["受试者清单与分组规则"]];
roster.getRange("A1").format = {
  fill: "#1F4E79",
  font: { bold: true, color: "#FFFFFF", size: 15 },
  horizontalAlignment: "center",
};
roster.getRange("A2:H2").values = [[
  "受试者编号",
  "编号范围",
  "分组",
  "L001-C1",
  "L001-C2",
  "L001-C3",
  "L001-C4",
  "D001",
]];

const rosterRows = [];
for (let i = 1; i <= 400; i += 1) {
  const g = groupNo(i);
  const rangeLabel = g === 1 ? "001-100" : g === 2 ? "101-200" : g === 3 ? "201-300" : "301-400";
  rosterRows.push([i, rangeLabel, `第${g}组`, "C1", `C2-${g}`, `C3-${g}`, "C4", "连续手势1/2"]);
}
roster.getRangeByIndexes(2, 0, rosterRows.length, 8).values = rosterRows;
const rosterTable = roster.tables.add(`A2:H${rosterRows.length + 2}`, true, "SubjectRosterTable");
rosterTable.style = "TableStyleMedium4";
roster.getRange("A2:H2").format = {
  fill: "#E2F0D9",
  font: { bold: true, color: "#375623" },
  horizontalAlignment: "center",
};
roster.getRange(`A3:A${rosterRows.length + 2}`).format.numberFormat = "000";
roster.getRange("A:A").format.columnWidth = 12;
roster.getRange("B:B").format.columnWidth = 13;
roster.getRange("C:C").format.columnWidth = 10;
roster.getRange("D:H").format.columnWidth = 13;
roster.freezePanes.freezeRows(2);

notes.getRange("A1:F1").merge();
notes.getRange("A1").values = [["表格说明"]];
notes.getRange("A1").format = {
  fill: "#1F4E79",
  font: { bold: true, color: "#FFFFFF", size: 15 },
  horizontalAlignment: "center",
};
notes.getRange("A3:B12").values = [
  ["总受试者数", 400],
  ["主表布局", "按打印页顺序分页双栏：每页左 10 人、右 10 人"],
  ["每人任务行数", 6],
  ["总任务行数", 2400],
  ["Lxxx", "离散手势，xxx 为受试者编号，包含 C1、C2-x、C3-x、C4"],
  ["Dxxx", "连续手势，xxx 为受试者编号，包含 连续手势1、连续手势2"],
  ["001-100", "C2-1 / C3-1"],
  ["101-200", "C2-2 / C3-2"],
  ["201-300", "C2-3 / C3-3"],
  ["301-400", "C2-4 / C3-4"],
];
notes.getRange("A3:A12").format = {
  fill: "#F3F4F6",
  font: { bold: true },
};
notes.getRange("A3:B12").format = {
  borders: { preset: "all", style: "thin", color: "#D1D5DB" },
  verticalAlignment: "center",
};
notes.getRange("A:A").format.columnWidth = 16;
notes.getRange("B:B").format.columnWidth = 48;

const preview = await workbook.render({
  sheetName: "任务进度表",
  range: "A1:M66",
  scale: 1,
  format: "png",
});
await fs.mkdir(outputDir, { recursive: true });
await fs.writeFile(path.join(outputDir, "任务进度表预览.png"), new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

console.log(outputPath);
