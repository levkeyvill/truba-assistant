const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('ui/web/pult.js', 'utf8');
const start = source.indexOf('function комГруппы(');
const end = source.indexOf('\nfunction комСекция(', start);
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context);
const guide = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const groups = context.комГруппы(guide);
const cards = Array.from(groups.flatMap(group => group.items));
const sources = Array.from(cards.flatMap(card => card.sources));
assert.equal(groups.length, 4);
assert.equal(new Set(cards.map(card => card.id)).size, cards.length);
assert.equal(new Set(cards.map(card => card.title)).size, cards.length);
assert.equal(new Set(sources).size, sources.length);
const expected = [...guide.commands.map(c => 'command:' + c.id), ...guide.skills.map(s => 'skill:' + s.title)];
assert.deepEqual(sources.sort(), expected.sort());
for (const card of cards) {
  assert(card.does.trim());
  assert(card.examples.length >= 1 && card.examples.length <= 4, card.title);
  assert.equal(new Set(card.examples).size, card.examples.length);
}
const find = id => cards.find(card => card.id === id);
assert.equal(find('browser').mode, 'local');
assert.equal(find('research').mode, 'ai');
assert(find('browser').examples.some(text => text.includes('найди в инете')));
assert(find('research').examples.some(text => text.includes('найди и расскажи')));
assert(find('documents').does.includes('RTF'));
assert(find('documents').does.includes('продолжи читать'));
assert.equal(cards.filter(card => card.id === 'youtube').length, 1);
assert.equal(cards.filter(card => card.id === 'apps').length, 1);
assert.equal(find('power').mode, 'confirm');
// Обновлённые данные и новые действия сервера не пропадают из справочника.
guide.commands.find(c => c.id === 'browser_search').examples[0] = 'Новый пример';
guide.commands.push({id:'future',title:'Новое действие',does:'Описание',examples:['сделай новое']});
guide.skills.push({title:'Новый навык',does:'Описание навыка',examples:['расскажи новое']});
const updated = context.комГруппы(guide).flatMap(group => group.items);
assert(updated.find(card => card.id === 'browser').examples.includes('Новый пример'));
assert(updated.some(card => card.id === 'future'));
assert(updated.some(card => card.title === 'Новый навык'));
// Отсутствующие записи не оставляют пустых карточек и не роняют страницу.
assert.equal(context.комГруппы({commands:[],skills:[]}).length, 0);
console.log(JSON.stringify({cards:cards.length, sources:sources.length, passed:8}));
