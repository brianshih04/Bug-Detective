"""Tests for renderMarkdown XSS protection in public/app.js.

This file contains the JS renderMarkdown function extracted for testing.
We evaluate it via a minimal JS runtime approach: extract the function
and test with known XSS payloads.
"""
import json
import subprocess

# Extracted renderMarkdown + escapeHtml from app.js for testing
RENDER_MD_JS = """
function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function renderMarkdown(text) {
    if (!text) return '';
    var html = text;

    html = html.replace(/```(\\w*)\\n([\\s\\S]*?)```/g, function(_, lang, code) {
      return '<pre><code>' + escapeHtml(code.trim()) + '</code></pre>';
    });
    html = html.replace(/```(\\w*)\\n([\\s\\S]*)$/g, function(_, lang, code) {
      return '<pre><code>' + escapeHtml(code.trimEnd()) + '</code></pre>';
    });

    html = html.replace(/`([^`]+)`/g, function(_, code) { return '<code>' + escapeHtml(code) + '</code>'; });

    html = html.replace(/^######\\s+(.+)$/gm, '<h6>$1</h6>');
    html = html.replace(/^#####\\s+(.+)$/gm, '<h5>$1</h5>');
    html = html.replace(/^####\\s+(.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^###\\s+(.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^##\\s+(.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^#\\s+(.+)$/gm, '<h1>$1</h1>');

    html = html.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
    html = html.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
    html = html.replace(/~~(.+?)~~/g, '<del>$1</del>');

    // XSS: block javascript:/data:/vbscript: schemes, escape link text and URL
    html = html.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, function(_, text, url) {
      if (/^\\s*(javascript|data|vbscript)\\s*:/i.test(url)) return escapeHtml(text);
      return '<a href=\"' + escapeHtml(url) + '\" target=\"_blank\" rel=\"noopener\">' + escapeHtml(text) + '</a>';
    });

    html = html.replace(/^>\\s+(.+)$/gm, '<blockquote>$1</blockquote>');

    return html;
}
"""


def _run_js(input_text):
    """Run renderMarkdown via Node.js and return the result."""
    full_js = RENDER_MD_JS + "\nconsole.log(JSON.stringify(renderMarkdown(" + json.dumps(input_text) + ")));"
    result = subprocess.run(
        ["node", "-e", full_js],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise RuntimeError(f"JS error: {result.stderr}")
    return json.loads(result.stdout.strip())


class TestRenderMarkdownXSS:
    """Verify XSS payloads in markdown are properly escaped."""

    # --- Link XSS ---
    def test_javascript_uri_blocked(self):
        html = _run_js("[click me](javascript:alert(1))")
        assert "javascript:" not in html.lower()
        assert "alert" not in html

    def test_javascript_with_spaces_blocked(self):
        html = _run_js("[click](  javascript  :  alert(1)  )")
        assert "javascript" not in html.lower() or "href" not in html

    def test_data_uri_blocked(self):
        html = _run_js("[click](data:text/html,<script>alert(1)</script>)")
        assert "data:" not in html.lower() or "href" not in html

    def test_vbscript_uri_blocked(self):
        html = _run_js("[click](vbscript:MsgBox(1))")
        assert "vbscript:" not in html.lower() or "href" not in html

    def test_link_text_html_escaped(self):
        """HTML in link text should be escaped, so no actual tags are injected."""
        html = _run_js('[<img src=x onerror=alert(1)>](http://example.com)')
        # The text is inside an <a> tag, but <img> should NOT be a real tag
        assert "<img" not in html
        # onerror appears in escaped text but is harmless (inside &lt;&gt;)
        # Verify the actual structure: <a href="...">&lt;img src=x onerror=alert(1)&gt;</a>
        assert "&lt;img" in html
        assert "&gt;" in html

    def test_link_url_attribute_injection_blocked(self):
        """Double-quote in URL should be escaped to &quot;, preventing real attribute injection."""
        html = _run_js('[click](foo" onmouseover="alert(1))')
        assert '&quot;' in html
        # onmouseover may appear in the escaped text but is inside &quot; so it's harmless
        # The key check: the href attribute value is properly escaped
        assert 'href="foo&quot;' in html

    def test_normal_link_works(self):
        html = _run_js("[Google](https://google.com)")
        assert 'href="https://google.com"' in html
        assert "Google" in html
        assert 'target="_blank"' in html

    # --- Code block XSS ---
    def test_code_block_escaped(self):
        html = _run_js("```html\n<script>alert('xss')</script>\n```")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_inline_code_escaped(self):
        html = _run_js("`<script>alert(1)</script>`")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    # --- General ---
    def test_empty_input(self):
        assert _run_js("") == ""

    def test_none_input(self):
        assert _run_js(None) == ""

    def test_plain_text_unchanged(self):
        text = "Hello world, this is plain text."
        assert _run_js(text) == text

    def test_bold_and_italic(self):
        html = _run_js("**bold** and *italic*")
        assert "<strong>bold</strong>" in html
        assert "<em>italic</em>" in html
