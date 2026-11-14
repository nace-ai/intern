import {existsSync,readFileSync,readdirSync,statSync} from 'node:fs';
import {parse} from 'dotenv';
const env=existsSync('.env')?parse(readFileSync('.env')):{};
const referenced=env.NACE_ENV_FILE && existsSync(env.NACE_ENV_FILE)?parse(readFileSync(env.NACE_ENV_FILE)):{};
const secrets=[...new Set(Object.entries({...referenced,...env,...process.env}).filter(([name,value])=>/(?:API_KEY|ACCESS_TOKEN)$/.test(name)&&typeof value==='string'&&value.length>15).map(([,value])=>value))];
function files(dir){return existsSync(dir)?readdirSync(dir).flatMap(name=>{const path=dir+'/'+name;return statSync(path).isDirectory()?files(path):[path];}):[];}
const paths=['src','dist','worker-dist','server','.private'].flatMap(files);
const leaks=paths.filter(path=>{const bytes=readFileSync(path);return secrets.some(value=>bytes.includes(Buffer.from(value)));});
if(leaks.length){console.error('Credential material found in: '+leaks.join(', '));process.exitCode=1;}else console.log('PASS: '+paths.length+' files checked against configured credential values; no credentials found.');
const frontend=files('dist').map(path=>readFileSync(path,'utf8')).join('\n');
if(/HEIST\{|nace_sk_[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9]{16,}|NaceClient|console\.nace\.ai\/v1/.test(frontend)){console.error('Unexpected secret or provider client in frontend bundle');process.exitCode=1;}else console.log('PASS: public assets contain no flag values, Nace SDK, or provider credentials.');
