import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root=process.argv[2];
if(!root) throw new Error('Pass the demo package directory');
const data=JSON.parse(await fs.readFile(path.join(root,'bom-data.json'),'utf8'));
for (const [revision,rows] of Object.entries(data.revisions)) {
  const wb=Workbook.create();
  const bom=wb.worksheets.add('Equipment BOM');
  const issue=wb.worksheets.add('Issue information');
  const values=[data.headers,...rows.map(row=>data.headers.map(key=>row[key]??null))];
  bom.getRange('A1:G8').values=values;
  bom.getRange('A1:G8').format={font:{name:'Arial',size:11,color:'#142B39'},rowHeight:40,verticalAlignment:'center',wrapText:true};
  bom.getRange('A1:G1').format={fill:'#142B39',font:{bold:true,color:'#FFFFFF'},rowHeight:30};
  for(let r=2;r<=8;r+=2) bom.getRange(`A${r}:G${r}`).format.fill='#F0F3F5';
  for(const [col,width] of [['A',18],['B',52],['C',14],['D',14],['E',23],['F',12],['G',60]]) bom.getRange(`${col}:${col}`).format.columnWidth=width;
  bom.getRange('C2:D8').setNumberFormat('0');bom.getRange('F2:F8').setNumberFormat('0');
  bom.freezePanes.freezeRows(1);bom.showGridLines=false;
  const details=[['FICTIONAL DEMO',data.disclaimer],['Job',data.job],['Revision',revision],['Status','For review; not released'],['Input','480 V / 3 PH / 60 Hz'],['Transformer','75 kVA / 480 D - 208Y/120 V'],['Catalog','All RVT-* part numbers are invented. No manufacturer compatibility implied.'],['Authority','Not an engineering approval or production package.'],['Known demo condition',revision==='B'?'One customer nameplate request is deliberately missed.':'Original issue for customer comment.']];
  issue.getRange('A1:B9').values=details;issue.showGridLines=false;
  issue.getRange('A1:B9').format={font:{name:'Arial',size:11,color:'#142B39'},rowHeight:36,wrapText:true,verticalAlignment:'center'};
  issue.getRange('A:A').format.columnWidth=26;issue.getRange('B:B').format.columnWidth=105;
  issue.getRange('A1:B1').format={fill:'#142B39',font:{bold:true,color:'#FFFFFF'},rowHeight:44};
  wb.recalculate();
  const inspected=await wb.inspect({kind:'region',sheetId:'Equipment BOM',range:'A1:G8',maxChars:1800,tableMaxRows:8,tableMaxCols:7});
  await fs.writeFile(path.join(root,`BOM_Rev-${revision}_inspection.json`),JSON.stringify(inspected,null,2));
  for(const sheetName of ['Equipment BOM','Issue information']) {
    const png=await wb.render({sheetName,range:sheetName==='Equipment BOM'?'A1:G8':'A1:B9',scale:1,format:'png'});
    await fs.writeFile(path.join(root,`BOM_Rev-${revision}_${sheetName.replaceAll(' ','-')}.png`),new Uint8Array(await png.arrayBuffer()));
  }
  await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(root,`PDU-75_BOM_Rev-${revision}.xlsx`));
}
console.log('Rev A and B BOM workbooks exported and rendered.');
