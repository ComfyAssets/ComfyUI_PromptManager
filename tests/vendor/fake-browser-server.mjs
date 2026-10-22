// A stand-in for tests/browser/server.py: `--port N` (0 = an OS-chosen port)
// serves the readiness page and announces the bound port on stdout as
// `PM_TEST_SERVER_PORT=<n>`, `--fail` writes to stderr and exits before
// listening, and GET /__exit makes the process die unexpectedly after writing a
// last stderr line. GET /__stderr?text=T writes T to stderr before answering.
// `--split-announce` writes the announcement in two chunks, the first ending
// mid-number. `--exit-on-stdin-eof` exits when its stdin closes, as server.py does.
import { createServer } from 'node:http';

const args = process.argv.slice(2);
const port = Number(args[args.indexOf('--port') + 1]);
if (args.includes('--fail')) {
    process.stderr.write('fake server: refusing to start\nlast fake stderr line\n');
    process.exit(4);
}
const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://fake');
    if (url.pathname === '/__stderr') {
        process.stderr.write(url.searchParams.get('text'));
        response.end('written');
        return;
    }
    if (request.url === '/__exit') {
        process.stderr.write('fake server: crashed on purpose\n');
        response.end('bye', () => process.exit(3));
        return;
    }
    response.end(request.url === '/prompt_manager/test' ? 'ready' : `pid ${process.pid}`);
});
server.listen(port, '127.0.0.1', () => {
    const line = `PM_TEST_SERVER_PORT=${server.address().port}\n`;
    if (!args.includes('--split-announce')) {
        process.stdout.write(`fake server: listening\n${line}`);
        return;
    }
    // The first chunk is a complete-looking number, one digit short of the real port.
    process.stdout.write(`fake server: listening\n${line.slice(0, -2)}`);
    setTimeout(() => process.stdout.write(line.slice(-2)), 300);
});
if (args.includes('--exit-on-stdin-eof')) {
    process.stdin.resume();
    process.stdin.once('end', () => process.exit(0));
}
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => server.close(() => process.exit(0)));
