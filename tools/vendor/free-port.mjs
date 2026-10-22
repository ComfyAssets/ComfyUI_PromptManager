import { createServer } from 'node:net';

// Ask the OS for a port nobody holds: bind port 0 on loopback, read it back and
// release it. A port another process has bound is never returned. The caller
// binds it next; the short window in between is covered by retrying a startup
// that fails with "address already in use" (see createServerSupervisor).
export function freePort(host = '127.0.0.1') {
    return new Promise((resolve, reject) => {
        const server = createServer();
        server.unref();
        server.once('error', reject);
        server.listen(0, host, () => {
            const { port } = server.address();
            server.close(error => (error ? reject(error) : resolve(port)));
        });
    });
}
