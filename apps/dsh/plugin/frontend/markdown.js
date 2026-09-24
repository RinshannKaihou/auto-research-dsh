/** Small, safe Markdown fallback for DSH builds without a public MarkdownText export. */
export function renderResearchMarkdown(React, source, {proseHeadings=false}={}) {
  const h=React.createElement, lines=String(source??'').replace(/\r\n?/g,'\n').split('\n'), blocks=[];
  const inline=value=>String(value).split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*]+\*)/g).filter(Boolean).map((part,index)=>{
    if(part.startsWith('**')&&part.endsWith('**'))return h('strong',{key:index},part.slice(2,-2));
    if(part.startsWith('`')&&part.endsWith('`'))return h('code',{key:index},part.slice(1,-1));
    if(part.startsWith('*')&&part.endsWith('*'))return h('em',{key:index},part.slice(1,-1));
    const link=part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if(link&&/^https?:\/\//i.test(link[2]))return h('a',{key:index,href:link[2],target:'_blank',rel:'noopener noreferrer'},link[1]);
    return part;
  });
  const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').map(cell=>cell.trim());
  const tableRule=line=>/^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$/.test(line);
  const bullet=line=>/^\s*(?:[-*+] |\d+\. )/.test(line);
  const special=line=>/^\s*(?:#{1,6} |```|> |---+\s*$)/.test(line)||bullet(line);
  for(let i=0;i<lines.length;){
    const line=lines[i],key=`block-${i}`;
    if(!line.trim()){i++;continue;}
    if(line.trimStart().startsWith('```')){
      const lang=line.trim().slice(3),code=[];i++;
      while(i<lines.length&&!lines[i].trimStart().startsWith('```'))code.push(lines[i++]);
      if(i<lines.length)i++;
      blocks.push(h('pre',{key,className:'ari-md-code'},h('code',{'data-language':lang},code.join('\n'))));continue;
    }
    const heading=line.match(/^\s*(#{1,6})\s+(.+)$/);
    if(heading){const tag=proseHeadings?(heading[2].length>120?'p':'h4'):`h${Math.min(heading[1].length,4)}`;blocks.push(h(tag,{key},...inline(heading[2])));i++;continue;}
    if(/^\s*---+\s*$/.test(line)){blocks.push(h('hr',{key}));i++;continue;}
    if(line.includes('|')&&i+1<lines.length&&tableRule(lines[i+1])){
      const headers=cells(line),rows=[];i+=2;
      while(i<lines.length&&lines[i].includes('|')&&lines[i].trim())rows.push(cells(lines[i++]));
      const head=h('thead',null,h('tr',null,...headers.map((cell,j)=>h('th',{key:j},...inline(cell)))));
      const body=h('tbody',null,...rows.map((row,j)=>h('tr',{key:j},...row.map((cell,k)=>h('td',{key:k},...inline(cell))))));
      blocks.push(h('div',{key,className:'ari-md-table-scroll'},h('table',null,head,body)));continue;
    }
    if(bullet(line)){
      const ordered=/^\s*\d+\. /.test(line),items=[];
      while(i<lines.length&&bullet(lines[i])&&(/^\s*\d+\. /.test(lines[i])===ordered))items.push(lines[i++].replace(/^\s*(?:[-*+] |\d+\. )/,''));
      blocks.push(h(ordered?'ol':'ul',{key},...items.map((item,j)=>h('li',{key:j},...inline(item)))));continue;
    }
    if(/^\s*> /.test(line)){
      const quote=[];while(i<lines.length&&/^\s*> /.test(lines[i]))quote.push(lines[i++].replace(/^\s*> /,''));
      blocks.push(h('blockquote',{key},...inline(quote.join(' '))));continue;
    }
    const paragraph=[line.trim()];i++;
    while(i<lines.length&&lines[i].trim()&&!special(lines[i])&&!(i+1<lines.length&&tableRule(lines[i+1])))paragraph.push(lines[i++].trim());
    blocks.push(h('p',{key},...inline(paragraph.join(' '))));
  }
  return h('article',{className:'ari-markdown'},...blocks);
}
