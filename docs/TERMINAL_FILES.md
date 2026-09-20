# Terminal file delivery

Admin-configured terminals can deliver generated files through authenticated links in chat.
`display_file` adds `download_url`, `owner_id`, and (for supported images) `image_url` after the
terminal confirms that the file exists. The model should copy these URLs into Markdown links
and images. The terminal file browser gets the same fields through `files/display`.

The delivery endpoints are:

- `GET /api/v1/terminals/{server_id}/files/download?ref=...`
- `GET /api/v1/terminals/{server_id}/files/image?ref=...`

The signed reference binds the generating user, terminal ID, chat/automation context, and absolute
path. It contains no login token or terminal credential. Every request requires the generating
user to be logged in, checks current terminal and chat access, and resolves terminal authentication
again. Sharing a chat or copying its links does not grant access to another user. Changing the
selected terminal does not change a link's destination.

Files are read from their original terminal path on each request. There are no stored copies,
database records, or independent link expiry. Replacing a file changes the delivered content;
deleting the file, losing access, changing the configured runtime context, or rotating
`WEBUI_SECRET_KEY` makes the link unavailable. Temporary chats follow the terminal's existing
context policy. Personal direct terminal connections retain their existing download behavior.

## Content isolation

Downloads always use `application/octet-stream` and `Content-Disposition: attachment`, including
HTML, JavaScript, PDF, and SVG. PDF and Office previews continue through client-side parsers.
Downloads stream without a file-size cap and close their upstream connection on completion or
interruption. The proxy uses a 10-second connection timeout and a 60-second idle read timeout.

Image previews accept PNG, JPEG, GIF, WebP, and SVG after checking the actual bytes. Image validation
buffers at most 32 MiB plus one overflow byte; larger images return 413 and remain downloadable.
Invalid or unsupported images return 415. SVG must have the SVG namespace/root, no DTD or XML
processing instructions, and pass XML parsing with entity resolution and network access disabled.

SVG previews use an `<img>` pointing at the protected HTTP URL. Source views show escaped text;
the application does not insert SVG file markup into the page. The response also carries an
unprivileged CSP `sandbox`, denies scripts, connections, frames, and forms, and permits only internal
styles and embedded image/font data. These response headers protect direct navigation as well as
image previews. See [SVG image restrictions](https://developer.mozilla.org/en-US/docs/Web/SVG/Guides/SVG_as_an_image)
and [CSP sandbox](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/sandbox).

All terminal file responses disable caching and MIME sniffing, restrict cross-origin embedding,
and reject browser cross-origin subresource requests (including CORS requests). They
discard untrusted upstream security headers, cookies, refresh headers, and redirects. The old
`files/view` and `files/serve` entry points apply the same content policy. Same-origin terminal
`proxy/{port}` web previews are disabled; HTML is available as source or a download. Terminal
control JSON/SSE and interactive sessions remain available. This policy is confined to terminal
content routes.

New signed results display inline file cards instead of opening the current file browser through
a path-only event. These cards offer preview, source viewing where applicable, and download. Their
Open-in-file-browser action is omitted because an ordinary file browser uses the current chat's
context, which can differ from the signed file's original automation or chat context.

## Verification

Run with the development dependencies and Python 3.11/3.12, Node 22:

```sh
python -m pytest backend/tests -q
npm run test:frontend -- --run
npm run check
```

The terminal policy/router tests isolate auth, storage, and upstream transport to avoid application
database side effects. Browser validation should use the real frontend components and HTTP router,
with synthetic users and files: normal SVG styles, scripts and event handlers, foreign content,
external resources, DTD/entity declarations, and masquerading HTML. Check chat images, expanded
images, file-browser source/preview, and direct navigation in Chromium and Firefox. Verify owner
access, denial for copied/shared links, original-context binding, download bytes, and document
previews against the deployment's configured terminal/auth modes as well.
