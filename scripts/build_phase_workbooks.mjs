// Run a temporary copy beside a symlink to the bundled artifact-tool runtime.
import fs from 'node:fs/promises';
import path from 'node:path';
import { SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const root = path.resolve(process.argv[2]);
const data = JSON.parse(await fs.readFile(path.join(root, 'workbooks.json'), 'utf8'));
for (const book of data.books) {
  const wb = Workbook.create();
  const sheet = wb.worksheets.add('BOM');
  sheet.showGridLines = false;
  sheet.getRange('A1:L37').format.font = {name: 'Arial', size: 10, color: '#272431'};
  sheet.getRange('A2').values = [[`Rivet / PDU-30 / BOM Rev ${book.revision}`]];
  sheet.getRange('A2').format.font = {name: 'Arial', size: 15, bold: true};
  sheet.getRange('A3').values = [['FICTIONAL DEMO. Candidate components; engineering approval and release remain pending.']];
  sheet.getRange('A3').format.font = {name: 'Arial', size: 10, italic: true, color: '#6D6579'};
  sheet.getRange('A5:L5').values = [book.headers];
  sheet.getRange(`A6:L${5 + book.values.length}`).values = book.values;
  const widths = [15, 21, 7, 68, 9, 9, 14, 16, 15, 22, 66, 12];
  for (let c = 0; c < widths.length; c++) {
    sheet.getRangeByIndexes(0, c, 37, 1).format.columnWidth = widths[c];
  }
  sheet.getRange('A5:L5').format = {fill: '#2C253A', font: {name: 'Arial', size: 10, bold: true, color: '#FFFFFF'}, horizontalAlignment: 'center', verticalAlignment: 'center', rowHeight: 28};
  sheet.getRange('A6:L26').format.rowHeight = 25;
  sheet.getRange('C6:C26').setNumberFormat('0');
  sheet.getRange('E6:I26').setNumberFormat('0');
  sheet.getRange('A6:L26').format.verticalAlignment = 'center';
  const table = sheet.tables.add(`A5:L${5+book.values.length}`, true, 'PduBom');
  table.style = 'TableStyleLight1';
  table.showBandedRows = true;
  sheet.freezePanes.freezeRows(5);
  sheet.getRange('A29').values = [['Sources and interpretation']];
  sheet.getRange('A29').format.font = {name: 'Arial', size: 11, bold: true};
  let row = 30;
  for (const [key, ref] of Object.entries(data.sources)) {
    sheet.getRange(`A${row}`).values = [[`${key}: ${ref.use}${ref.url ? ' Source: '+ref.url : ''}`]];
    sheet.getRange(`A${row}`).format.font = {name: 'Arial', size: 9, color: '#63596D'};
    sheet.getRange(`A${row}`).format.rowHeight = 22;
    row++;
  }
  wb.recalculate();
  const check = await wb.inspect({kind: 'table', range: 'BOM!A5:L8', include: 'values', tableMaxRows: 4, tableMaxCols: 12, maxChars: 2200});
  console.log(check.ndjson);
  const errors = await wb.inspect({kind:'match', searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#NULL!|#SPILL!|#CALC!', options:{useRegex:true,maxResults:20}, maxChars:800});
  console.log(errors.ndjson);
  const image = await wb.render({sheetName: 'BOM', range: 'A1:L37', scale: 1, format: 'png'});
  await fs.writeFile(path.join(root, `BOM-Rev-${book.revision}-preview.png`), new Uint8Array(await image.arrayBuffer()));
  const output = await SpreadsheetFile.exportXlsx(wb);
  await output.save(path.join(root, book.path));
}
