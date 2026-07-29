/**
 * Single-Page Application (SPA) AJAX Navigation Engine
 * Intercepts all link clicks and form submissions for student pages,
 * preserving media streams and WebRTC connections without page reloads.
 */

(function () {
    'use strict';

    // Global Link Interceptor
    document.addEventListener('click', function (e) {
        if (e.defaultPrevented) return;
        const link = e.target.closest('a');
        if (!link) return;

        const href = link.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:') || link.target === '_blank' || link.hasAttribute('download') || link.hasAttribute('data-no-ajax')) {
            return;
        }

        // Check if internal same-origin link
        const targetUrl = new URL(link.href, window.location.origin);
        if (targetUrl.origin !== window.location.origin) return;

        // Skip admin links if admin wants full reloads, but intercept student links
        if (targetUrl.pathname.startsWith('/admin') && !targetUrl.pathname.startsWith('/admin/proctor')) {
            return;
        }

        e.preventDefault();
        loadUrlViaAjax(targetUrl.href, true);
    });

    // Global Form Submission Interceptor
    document.addEventListener('submit', function (e) {
        const form = e.target;
        if (!form || form.target === '_blank' || form.hasAttribute('data-no-ajax')) return;

        const formAction = form.action || window.location.href;
        const targetUrl = new URL(formAction, window.location.origin);
        if (targetUrl.origin !== window.location.origin) return;

        e.preventDefault();
        submitFormViaAjax(form);
    });

    // Handle Browser Back / Forward buttons
    window.addEventListener('popstate', function () {
        loadUrlViaAjax(window.location.href, false);
    });

    /**
     * Load page content via AJAX fetch and swap DOM in-place.
     */
    async function loadUrlViaAjax(url, pushState) {
        try {
            const resp = await fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });

            if (resp.redirected) {
                url = resp.url;
            }

            const htmlText = await resp.text();
            swapMainContent(htmlText, url, pushState);
        } catch (err) {
            console.error('SPA Navigation Fetch Error:', err);
            window.location.href = url; // Fallback to full load if fetch fails
        }
    }

    /**
     * Submit form via AJAX fetch and swap DOM with response HTML.
     */
    async function submitFormViaAjax(form) {
        try {
            // Update hidden code input if Ace editor is present
            if (window.codeInput && window.editor) {
                window.codeInput.value = window.editor.getValue();
            }

            const formData = new FormData(form);
            const method = (form.method || 'POST').toUpperCase();
            let actionUrl = form.action || window.location.href;

            const opts = {
                method: method,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            };

            if (method === 'GET') {
                const params = new URLSearchParams(formData);
                actionUrl = actionUrl.split('?')[0] + '?' + params.toString();
            } else {
                opts.body = formData;
            }

            const resp = await fetch(actionUrl, opts);
            let finalUrl = resp.url || actionUrl;

            const htmlText = await resp.text();
            swapMainContent(htmlText, finalUrl, true);
        } catch (err) {
            console.error('SPA Form Submit Error:', err);
            form.submit(); // Fallback to standard submit
        }
    }

    /**
     * Swap main-content container, update document title, and re-execute scripts.
     */
    function swapMainContent(htmlText, url, pushState) {
        const parser = new DOMParser();
        const newDoc = parser.parseFromString(htmlText, 'text/html');

        // Update Document Title
        if (newDoc.title) {
            document.title = newDoc.title;
        }

        // Update Navbar container if present
        const newNav = newDoc.querySelector('.navbar');
        const currentNav = document.querySelector('.navbar');
        if (newNav && currentNav) {
            currentNav.innerHTML = newNav.innerHTML;
        }

        // Find main content container
        const newMain = newDoc.querySelector('main') || newDoc.querySelector('.main-content') || newDoc.querySelector('.container');
        const currentMain = document.querySelector('main') || document.querySelector('.main-content') || document.querySelector('.container');

        if (newMain && currentMain) {
            currentMain.innerHTML = newMain.innerHTML;
        } else {
            document.body.innerHTML = newDoc.body.innerHTML;
        }

        // Update URL Bar
        if (pushState) {
            history.pushState({ url: url }, '', url);
        }

        // Scroll to top
        window.scrollTo(0, 0);

        // Re-execute scripts in newly inserted content
        const scripts = (newMain || newDoc.body).querySelectorAll('script');
        const alreadyLoaded = new Set(
            Array.from(document.querySelectorAll('script[src]')).map(s => s.src)
        );
        scripts.forEach(s => {
            if (s.src) {
                // Skip scripts already loaded globally (e.g. proctor.js, ace.js)
                if (alreadyLoaded.has(s.src) || alreadyLoaded.has(new URL(s.src, location.origin).href)) return;
                const scriptTag = document.createElement('script');
                scriptTag.src = s.src;
                document.body.appendChild(scriptTag);
            } else {
                try {
                    (1, eval)(s.innerText);
                } catch (e) {
                    console.warn('SPA script re-eval warning:', e);
                }
            }
        });


        // Render KaTeX LaTeX math in newly inserted content
        if (typeof renderMathInElement === 'function') {
            try {
                renderMathInElement(document.body, {
                    delimiters: [
                        {left: '$$', right: '$$', display: true},
                        {left: '$', right: '$', display: false},
                        {left: '\\(', right: '\\)', display: false},
                        {left: '\\[', right: '\\]', display: true}
                    ],
                    throwOnError: false
                });
            } catch(e) {}
        }

        // Trigger custom page re-init event
        window.dispatchEvent(new CustomEvent('spa:page_loaded', { detail: { url: url } }));
    }

    window.spaNavigate = loadUrlViaAjax;
})();

