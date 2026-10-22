// A stand-in for tests/browser/server.py: `--port N` (0 = an OS-chosen port)
// serves the readiness page and announces the bound port on stdout as
// `PM_TEST_SERVER_PORT=<n>`, `--fail` writes to stderr and exits before
// listening, and GET /__exit makes the process die unexpectedly after writing a
// last stderr line.
import { createServer } from 'node:http';

const args = process.argv.slice(2);
const port = Number(args[args.indexOf('--port') + 1]);
if (args.includes('--fail')) {
    process.stderr.write('fake server: refusing to start\nlast fake stderr line\n');
    process.exit(4);
}
const server = createServer((request, response) => {
    if (request.url === '/__exit') {
        process.stderr.write('fake server: crashed on purpose\n');
        response.end('bye', () => process.exit(3));
        return;
    }
    response.end(request.url === '/prompt_manager/test' ? 'ready' : `pid ${process.pid}`);
});
server.listen(port, '127.0.0.1', () => {
    process.stdout.write(`fake server: listening\nPM_TEST_SERVER_PORT=${server.address().port}\n`);
});
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => server.close(() => process.exit(0)));
