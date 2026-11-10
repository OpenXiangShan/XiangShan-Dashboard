import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = readFileSync(new URL('../src/internal/metricSelection.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}});
const { toggleMetricGroup } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('selecting a category includes every member, independently of search pagination',()=>{
 const members={old_topdown:Array.from({length:140},(_,i)=>`counter${i}`)};
 const next=toggleMetricGroup(['cpi'],[],'old_topdown',members);
 assert.equal(next.metrics.length,141);
 assert.deepEqual(next.groups,['old_topdown']);
 const removed=toggleMetricGroup(next.metrics,next.groups,'old_topdown',members);
 assert.deepEqual(removed.metrics,['cpi']);
 assert.deepEqual(removed.groups,[]);
});
test('deselect preserves counters shared by another active category',()=>{
 const members={a:['shared','onlyA'],b:['shared','onlyB']};
 let next=toggleMetricGroup(['cpi'],[],'a',members);
 next=toggleMetricGroup(next.metrics,next.groups,'b',members);
 assert.equal(next.metrics.filter(x=>x==='shared').length,1);
 next=toggleMetricGroup(next.metrics,next.groups,'a',members);
 assert.deepEqual(next.metrics,['cpi','shared','onlyB']);
});
