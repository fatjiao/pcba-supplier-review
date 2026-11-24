import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { constants } from 'node:fs';
import { SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const colors = { navy:'#183A59', ink:'#223346', muted:'#50687E', pale:'#EAF2F8',
  edit:'#FFF2CF', red:'#FCE8E3', green:'#EBF4EE', gray:'#F1F4F7' };
const font = 'Microsoft YaHei';
const types = {schematic:'SCH',pcb:'PCB',bom:'BOM',checklist:'Checklist'};
const coverage = {complete:'已枚举',partial:'部分读取',unavailable:'未取得'};
const reasons = {
  verified:['OK',''], mismatch:['NG','已见差异。'], recommendation:['NG','建议项差异。'],
  missing:['NG','本次通过依据不足。'], 'not-applicable':['NC','不适用。'],
};
const selection = {合适:colors.green,不合适:colors.red,待确认:colors.edit,不适用:colors.gray};
const comparisonStatuses = [
  '图表差异','BOM独有','原理图独有','规格待核','装配预留','无位号料件',
  '可比字段相符','图示NC','测试点','编译遮罩',
];
const collator = new Intl.Collator('zh-CN',{numeric:true,sensitivity:'base'});

function check(condition, message) {
  if (!condition) throw new Error(message);
}

function requireText(value, name) {
  check(typeof value==='string' && value.trim(),'缺少 '+name+'。');
}

function unique(values, label) {
  check(Array.isArray(values),label+' 必须是列表。');
  const entries=new Set();
  for (const value of values) {
    requireText(value,label);
    check(!entries.has(value),label+' 有重复项 '+value+'。');
    entries.add(value);
  }
  return entries;
}

function compareSets(expected, actual, label) {
  const missing=[...expected].filter(value=>!actual.has(value));
  const extra=[...actual].filter(value=>!expected.has(value));
  check(!missing.length && !extra.length,label+' 与源清单不符。缺 '+(missing.slice(0,6).join('、')||'0')+
    '，多 '+(extra.slice(0,6).join('、')||'0')+'。');
}

function validate(input) {
  requireText(input.project,'项目名');
  requireText(input.scope,'核对范围与版次关系');
  for (const [key,label] of Object.entries(types)) {
    const source=input.sources?.[key];
    check(source,'缺少 '+label+' 来源记录。');
    for (const field of ['name','location','evidence']) requireText(source[field],label+' '+field);
    check(Object.hasOwn(coverage,source.coverage),label+' 覆盖状态不合法。');
    if (source.sha256!=null) check(/^[0-9a-f]{64}$/i.test(source.sha256),label+' SHA-256 不合法。');
  }
  for (const key of ['schematic','checklist','comparison']) {
    check(Array.isArray(input[key]),'缺少 '+key+' 明细列表。');
  }
  const sch=unique(input.inventory?.schematicRefs,'原理图位号清单');
  const checklist=unique(input.inventory?.checklistRows,'Checklist 原行清单');
  const lines=input.inventory?.bomLines;
  check(Array.isArray(lines),'缺少 BOM 原行清单。');
  unique(lines.map(row=>row.line),'BOM 原行键');
  for (const [key,count] of [['schematic',sch.size],['checklist',checklist.size],['bom',lines.length]]) {
    const state=input.sources[key].coverage;
    if (state==='unavailable') check(count===0,types[key]+' 标为未取得但清单非空。');
  }
  compareSets(sch,unique(input.schematic.map(row=>row.ref),'原理图评审位号'),'原理图评审');
  for (const row of input.schematic) {
    for (const key of ['module','role','calculation','evidence']) requireText(row[key],row.ref+' '+key);
    check(Object.hasOwn(selection,row.decision),row.ref+' 的选型结论不合法。');
    if (row.decision!=='合适') requireText(row.note,row.ref+' 选型备注');
  }
  compareSets(checklist,unique(input.checklist.map(row=>row.location),'Checklist 输出原行'),'Checklist');
  for (const row of input.checklist) {
    for (const key of ['id','requirement','evidence','note']) requireText(row[key],row.location+' '+key);
    check(Object.hasOwn(reasons,row.reason) && reasons[row.reason][0]===row.decision,
      row.location+' 的 AI 结论与原因类型不匹配。');
  }

  const pair=(line,ref)=>JSON.stringify([line,ref]);
  const expected=new Set(), bomRefs=new Set();
  for (const line of lines) {
    const refs=unique(line.refs,'BOM '+line.line+' 位号');
    for (const ref of refs) {
      expected.add(pair(line.line,ref));
      bomRefs.add(ref);
    }
    if (!refs.size) expected.add(pair(line.line,null));
  }
  const observed=new Set(), seenSch=new Set(), schOnly=new Set();
  for (const row of input.comparison) {
    const ref=row.ref??null, line=row.bomLine??null;
    const inSch=sch.has(ref), caption=ref??row.item??line;
    if (ref===null) requireText(row.item,'无位号料件名称');
    else requireText(ref,'对比位号');
    requireText(row.note,caption+' 对比说明');
    requireText(row.bomEvidence,caption+' BOM 位置或检索范围');
    if (!inSch) requireText(row.schematicEvidence,caption+' 原图检索范围或证据缺口');
    check(comparisonStatuses.includes(row.status),caption+' 的对比状态不合法。');
    if (line!==null) {
      requireText(line,'BOM 原行键');
      requireText(row.role,caption+' 料件角色');
      const key=pair(line,ref);
      check(!observed.has(key),'重复 BOM 原行与位号 '+key+'。');
      observed.add(key);
    } else {
      check(inSch && !bomRefs.has(ref) && !schOnly.has(ref),caption+' 缺少对应的 BOM 原行，或与其他核对行重复。');
      schOnly.add(ref);
    }
    if (inSch) seenSch.add(ref);
    if (row.status==='BOM独有') check(input.sources.schematic.coverage==='complete' && line!==null && ref && !inSch,
      caption+' 无法按现有覆盖判为 BOM 独有。');
    if (row.status==='原理图独有') check(input.sources.bom.coverage==='complete' && line===null && inSch,
      caption+' 无法按现有覆盖判为原理图独有。');
    if (row.status==='可比字段相符') check(line!==null && inSch,caption+' 缺少两侧可比记录。');
  }
  compareSets(expected,observed,'BOM 原行与位号');
  compareSets(sch,seenSch,'对照表中的原理图位号');
  return {lines,bomRefs};
}

function cell(value) {
  if (value==null || value==='') return null;
  if (typeof value!=='string') return value;
  const safe=value.startsWith('=')?"'"+value:value;
  check(safe.length<=32767,'一处内容超出 Excel 单元格容量，请保留来源并拆分其核对范围。');
  return safe;
}

function makeSheet(book,name,widths) {
  const sheet=book.worksheets.add(name);
  const columns='ABCDEFGHIJK'.slice(0,widths.length);
  sheet.showGridLines=false;
  sheet.tabColor=colors.navy;
  for (const [index,width] of widths.entries()) {
    sheet.getRange(columns[index]+'1').format.columnWidthPx=width;
  }
  return {sheet,widths,last:columns.at(-1)};
}

function band(view,row,text,options={}) {
  const range=view.sheet.getRange('A'+row+':'+view.last+row);
  range.merge();
  view.sheet.getRange('A'+row).values=[[cell(text)]];
  range.format.rowHeightPx=options.height??25;
  range.format.fill=options.fill??colors.pale;
  range.format.font={name:font,size:options.size??10,color:options.color??colors.muted,bold:options.bold??false};
  range.format.wrapText=true;
  range.format.verticalAlignment='center';
}

function heading(view,row,labels) {
  const range=view.sheet.getRange('A'+row+':'+String.fromCharCode(64+labels.length)+row);
  range.values=[labels];
  range.format.fill=colors.navy;
  range.format.font={name:font,size:10,color:'#FFFFFF',bold:true};
  range.format.horizontalAlignment='center';
  range.format.verticalAlignment='center';
  range.format.rowHeightPx=31;
}

function rowLines(value,width) {
  return String(value??'').split(/\r?\n/).reduce((total,line)=>{
    const pixels=Array.from(line).reduce((sum,ch)=>sum+(ch.charCodeAt(0)>255?14:7.6),0);
    return total+Math.max(1,Math.ceil(pixels/Math.max(40,width-28)));
  },0);
}

function writeRows(view,first,rows,minHeight) {
  if (!rows.length) return;
  const range=view.sheet.getRangeByIndexes(first-1,0,rows.length,rows[0].length);
  range.setNumberFormat('@');
  range.values=rows.map(row=>row.map(cell));
  range.format.font={name:font,size:10,color:colors.ink};
  range.format.verticalAlignment='center';
  range.format.wrapText=true;
  range.format.rowHeightPx=minHeight;
  for (const [i,row] of rows.entries()) {
    const lines=Math.max(...row.map((part,j)=>rowLines(part,view.widths[j])));
    const height=Math.min(530,lines*17+10);
    if (height>minHeight) view.sheet.getRange('A'+(first+i)).format.rowHeightPx=height;
  }
}

function addTable(view,headerRow,length,name,labels,engineerColumn) {
  const last=headerRow+length;
  if (length) {
    const table=view.sheet.tables.add('A'+headerRow+':'+view.last+last,true,name);
    table.style='TableStyleLight1';
    table.showFilterButton=true;
    view.sheet.getRange(engineerColumn+(headerRow+1)+':'+engineerColumn+last).format.fill=colors.edit;
  }
  heading(view,headerRow,labels);
}

function sourceText(input,key) {
  const source=input.sources[key];
  return [types[key]+'  '+source.name,source.revision,source.location,coverage[source.coverage],source.evidence]
    .filter(Boolean).join('　');
}

function title(view,input,caption) {
  band(view,1,input.project+' · '+caption,{height:38,fill:colors.navy,size:15,color:'#FFFFFF',bold:true});
}

function groupRows(rows,key) {
  const groups=new Map();
  for (const row of rows) {
    const name=key(row);
    if (!groups.has(name)) groups.set(name,[]);
    groups.get(name).push(row);
  }
  return groups;
}

function codeColors(sheet,column,first,last,palette) {
  const range=sheet.getRange(column+first+':'+column+last);
  range.format.horizontalAlignment='center';
  for (const [code,fill] of Object.entries(palette)) {
    range.conditionalFormats.addCustom('=$'+column+first+'="'+code+'"',{fill});
  }
  return range;
}

function schematicSheet(book,input) {
  const view=makeSheet(book,'原理图评审',[187,86,124,265,162,183,420,116,385,290,168]);
  title(view,input,'原理图评审');
  band(view,2,sourceText(input,'schematic'),{height:34});
  band(view,3,sourceText(input,'pcb'),{height:34});
  band(view,4,input.scope,{height:34,fill:colors.edit});
  const modules=groupRows(input.schematic,row=>row.module);
  const details=[...modules.values()].flatMap(rows=>[...rows].sort((a,b)=>collator.compare(a.ref,b.ref)));
  band(view,5,'已列 '+modules.size+' 个功能模块、'+details.length+' 个位号。');
  const moduleRow=7, detailHeader=moduleRow+modules.size+3, first=detailHeader+1, last=detailHeader+details.length;
  heading(view,6,['功能模块','图面位号','待确认','模块位置与选型关注点']);
  if (modules.size) {
    const data=[...modules.keys()].map(name=>[name,null,null,input.moduleNotes?.[name]??null]);
    writeRows(view,moduleRow,data,28);
    const domain='$A$'+first+':$A$'+last, codes='$H$'+first+':$H$'+last;
    const totals=view.sheet.getRange('B'+moduleRow+':C'+(moduleRow+modules.size-1));
    totals.setNumberFormat('0');
    totals.formulas=data.map((_,i)=>[
      '=COUNTIF('+domain+',$A'+(moduleRow+i)+')',
      '=COUNTIFS('+domain+',$A'+(moduleRow+i)+','+codes+',"待确认")',
    ]);
  }
  band(view,detailHeader-1,'逐器件选型评审',{bold:true});
  const labels=['功能模块','位号','计算／复核状态','原理图器件参数','原理图封装','器件用途',
    '计算条件与算式','AI选型结论','原因／备注','原图定位／依据','工程师审核结论'];
  writeRows(view,first,details.map(row=>[
    row.module,row.ref,row.state??(row.decision==='待确认'?'优先复核':'图示工况'),
    row.value,row.package,row.role,row.calculation,row.decision,row.note,row.evidence,null,
  ]),51);
  addTable(view,detailHeader,details.length,'SchematicSelection',labels,'K');
  if (details.length) codeColors(view.sheet,'H',first,last,selection);
  else band(view,first,'本次没有可列的原理图位号。读取状态和补件见上方来源记录。');
  view.sheet.freezePanes.freezeRows(Math.min(detailHeader,7));
  view.sheet.freezePanes.freezeColumns(2);
  return {view,moduleRow,modules:modules.size,details:details.length,first,detailHeader};
}

async function checklistSheet(book,input,inputDirectory) {
  const view=makeSheet(book,'原理图Checklist',[78,76,520,72,73,255,85,510]);
  title(view,input,'原理图 Checklist');
  band(view,2,sourceText(input,'checklist'),{height:34});
  band(view,3,'NC 仅用于明确不适用，适用项判 OK 或 NG。NG 的差异或证据缺口见 AI备注。HW、评审和 Remarks 为源表历史字段。',{height:34});
  const groups=[];
  for (const item of input.checklist) {
    const section=item.section||'通用';
    if (groups.at(-1)?.section!==section) groups.push({section,entries:[]});
    groups.at(-1).entries.push(item);
  }
  const labels=['No.','标准','Description','HW','评审','Remarks','AI结论','AI备注'];
  let row=4;
  for (const {section,entries} of groups) {
    band(view,row++,section,{bold:true,height:28});
    heading(view,row++,labels);
    const first=row, last=row+entries.length-1;
    writeRows(view,first,entries.map(item=>[
      item.id,item.level,item.requirement,item.originalHw,item.originalReview,item.originalRemarks,item.decision,
      [reasons[item.reason][1]+item.note,item.location,item.evidence].join('\n'),
    ]),42);
    const history=view.sheet.getRange('D'+first+':F'+last);
    history.format.fill=colors.gray;
    history.format.font={name:font,size:10,color:colors.muted};
    const result=codeColors(view.sheet,'G',first,last,{OK:colors.green,NG:colors.red,NC:colors.gray});
    result.dataValidation={
      rule:{type:'list',values:['NC','NG','OK']},
      errorAlert:{style:'stop',title:'AI结论',message:'请选择 NC、NG 或 OK。'},
    };
    row=last+1;
  }
  if (!groups.length) {
    band(view,row++,'Checklist 覆盖缺口',{bold:true});
    heading(view,row++,labels);
    band(view,row++,'本次没有可列的 Checklist 条款。资料预检未形成条款结论，覆盖率不可计算。读取状态和补件见上方来源记录。');
  }
  for (const figure of input.checklistFigures??[]) {
    for (const key of ['file','caption','evidence']) requireText(figure[key],'Checklist 例图 '+key);
    check(Number.isSafeInteger(figure.widthPx) && figure.widthPx>0 &&
      Number.isSafeInteger(figure.heightPx) && figure.heightPx>0,'Checklist 例图须有可核实的像素尺寸。');
    const file=path.resolve(inputDirectory,figure.file);
    const extension=path.extname(file).toLowerCase();
    check(['.png','.jpg','.jpeg'].includes(extension),'Checklist 例图须为 PNG 或 JPEG。');
    const data=await fs.readFile(file);
    const mime=extension==='.png'?'png':'jpeg';
    const scale=Math.min(1,1200/figure.widthPx,850/figure.heightPx);
    const widthPx=Math.round(figure.widthPx*scale);
    const heightPx=Math.round(figure.heightPx*scale);
    band(view,++row,'原表例图  '+figure.caption+'　'+figure.evidence,{bold:true,height:30});
    view.sheet.images.add({
      dataUrl:'data:image/'+mime+';base64,'+data.toString('base64'),
      anchor:{from:{row,col:0},extent:{widthPx,heightPx}},
    });
    row+=Math.ceil(heightPx/18)+2;
  }
  view.sheet.freezePanes.freezeRows(5);
  return view;
}

function comparisonSheet(book,input,inventory) {
  const view=makeSheet(book,'原理图与BOM',[96,234,146,205,345,222,190,162,463,168]);
  const sch=new Map(input.schematic.map(row=>[row.ref,row]));
  const rows=[...input.comparison].sort((a,b)=>
    comparisonStatuses.indexOf(a.status)-comparisonStatuses.indexOf(b.status) ||
    collator.compare(a.ref??a.item,b.ref??b.item) || collator.compare(a.bomLine??'',b.bomLine??''));
  const matched=[...inventory.bomRefs].filter(ref=>sch.has(ref)).length;
  title(view,input,'原理图与 BOM 逐位号核对');
  band(view,2,sourceText(input,'bom'),{height:34});
  band(view,3,sourceText(input,'schematic'),{height:34});
  band(view,4,sourceText(input,'pcb'),{height:34});
  band(view,5,input.scope,{height:34,fill:colors.edit});
  band(view,6,'已列 '+inventory.lines.length+' 个 BOM 原行、'+inventory.bomRefs.size+' 个 BOM 位号、'+sch.size+
    ' 个原理图位号。两侧共有 '+matched+' 个位号，共 '+rows.length+' 条明细。');
  band(view,7,'BOM 用量按原行记录。替代料、预留、无位号料件和单侧位号分别列示。');
  const labels=['位号','原理图器件参数','图示封装／外形','原理图定位','BOM器件描述',
    'BOM封装／原厂型号','BOM原表位置','料件角色／用量','AI审核结论','工程师审核结论'];
  const headerRow=8, first=headerRow+1, last=headerRow+rows.length;
  writeRows(view,first,rows.map(row=>{
    const part=sch.get(row.ref);
    return [row.ref??row.item,part?.value,part?.package,row.schematicEvidence||part?.evidence,
      row.bomDescription,[row.bomPackage,row.mpn].filter(Boolean).join('\n'),row.bomEvidence,row.role,
      row.status+'。'+row.note,null];
  }),49);
  addTable(view,headerRow,rows.length,'SchematicBomComparison',labels,'J');
  if (rows.length) {
    const result=view.sheet.getRange('I'+first+':I'+last);
    result.conditionalFormats.addCustom('=LEFT($I'+first+',4)="图表差异"',{fill:colors.red});
    result.conditionalFormats.addCustom('=OR(LEFT($I'+first+',5)="BOM独有",LEFT($I'+first+
      ',5)="原理图独有",LEFT($I'+first+',4)="规格待核")',{fill:colors.edit});
  } else band(view,first,'本次没有可列的 BOM 或原理图明细。读取状态和补件见上方来源记录。');
  const fingerprints=Object.entries(input.sources).filter(([,source])=>source.sha256!=null);
  for (const [index,[key,source]] of fingerprints.entries()) {
    if (Object.hasOwn(types,key)) band(view,Math.max(first,last)+2+index,types[key]+' 文件视图 SHA-256  '+source.sha256);
  }
  view.sheet.freezePanes.freezeRows(headerRow);
  view.sheet.freezePanes.freezeColumns(1);
  return view;
}

async function main() {
  const [,, dataPath,outputPath,previewDirectory,...extra]=process.argv;
  check(dataPath && outputPath && !extra.length,'用法：node build-review.mjs review.json output.xlsx [preview-directory]');
  check(path.extname(outputPath).toLowerCase()==='.xlsx','输出文件必须使用 .xlsx 扩展名。');
  const input=JSON.parse((await fs.readFile(dataPath,'utf8')).replace(/^\uFEFF/,''));
  const inventory=validate(input);
  const book=Workbook.create();
  const schematic=schematicSheet(book,input);
  const checklist=await checklistSheet(book,input,path.dirname(path.resolve(dataPath)));
  const comparison=comparisonSheet(book,input,inventory);
  book.recalculate();
  if (schematic.modules) {
    const last=schematic.moduleRow+schematic.modules-1;
    const values=schematic.view.sheet.getRange('B'+schematic.moduleRow+':C'+last).values;
    const pending=input.schematic.filter(row=>row.decision==='待确认').length;
    check(values.reduce((n,row)=>n+row[0],0)===schematic.details && values.reduce((n,row)=>n+row[1],0)===pending,
      '原理图模块汇总与逐器件明细不符。');
  }
  if (previewDirectory) {
    await fs.mkdir(previewDirectory,{recursive:true});
    for (const [view,name,range] of [
      [schematic.view,'schematic','A1:K8'],
      [schematic.view,'schematic-detail','A'+schematic.detailHeader+':K'+(schematic.first+2)],
      [checklist,'checklist','A1:H9'],
      [comparison,'comparison','A1:J12'],
    ]) {
      const png=await book.render({sheetName:view.sheet.name,range,scale:0.85,format:'png'});
      await fs.writeFile(path.join(previewDirectory,name+'.png'),new Uint8Array(await png.arrayBuffer()));
    }
  }
  const output=path.resolve(outputPath);
  await fs.mkdir(path.dirname(output),{recursive:true});
  const scratch=await fs.mkdtemp(path.join(os.tmpdir(),'pcba-review-export-'));
  const candidate=path.join(scratch,'report.xlsx');
  try {
    const xlsx=await SpreadsheetFile.exportXlsx(book);
    await xlsx.save(candidate);
    await fs.copyFile(candidate,output,constants.COPYFILE_EXCL);
  } finally {
    await fs.rm(candidate,{force:true});
    await fs.rm(candidate+'.inspect.ndjson',{force:true});
    await fs.rmdir(scratch);
  }
  console.log(output);
}

try {
  await main();
} catch (error) {
  console.error(error.message);
  process.exitCode=1;
}
