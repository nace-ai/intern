// Short labels for tight spots; the full name goes in a tooltip.
const SHORT: [string, string][] = [
  ["Double bass", "D. bass"],
  ["Snare drum", "Snare"],
  ["Bass drum", "B. drum"],
  ["French horn", "Horn"],
  ["Xylophone", "Xylo."],
  ["Harmonium", "Harm."],
];

export function shortName(name: string): string {
  const hit = SHORT.find(([long]) => name.startsWith(long));
  return hit ? hit[1] + name.slice(hit[0].length) : name;
}
