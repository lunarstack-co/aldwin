# Report

<script>window.__xss = "script ran"</script>

<img src="x" onerror="window.__xss = 'img ran'">

<iframe src="https://example.com/"></iframe>

<a href="javascript:window.__xss='link ran'">bad link</a>

[a real link](https://example.com/page)

<div onclick="window.__xss='div ran'">clickable</div>

Plain text with `code` and **bold** survives.
