/**
 * AAST CodeJudge — Ace Editor Integration
 * Initialises the code editor and handles language switching / form submission.
 */

document.addEventListener("DOMContentLoaded", function () {
    const editorEl = document.getElementById("code-editor");
    if (!editorEl) return;

    // ── Initialise Ace ──────────────────────────────────────────────
    const editor = ace.edit("code-editor");
    editor.setTheme("ace/theme/one_dark");
    editor.setOptions({
        fontSize: "14px",
        showPrintMargin: false,
        wrap: true,
        tabSize: 4,
        useSoftTabs: true,
        enableBasicAutocompletion: true,
        enableLiveAutocompletion: true,
    });

    // ── Language → Ace mode mapping ─────────────────────────────────
    const langModes = {
        python: "ace/mode/python",
        c: "ace/mode/c_cpp",
        cpp: "ace/mode/c_cpp",
    };

    // ── Default code templates ──────────────────────────────────────
    const templates = {
        python: '# Your solution here\nimport sys\ninput_data = sys.stdin.read().split()\n\n',
        c: '#include <stdio.h>\n\nint main() {\n    \n    return 0;\n}\n',
        cpp: '#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios_base::sync_with_stdio(false);\n    cin.tie(NULL);\n    \n    return 0;\n}\n',
    };

    // ── Language selector ───────────────────────────────────────────
    const langSelect = document.getElementById("language-select");

    function setLanguage(lang) {
        const mode = langModes[lang] || "ace/mode/text";
        editor.session.setMode(mode);

        // Only set template if editor is empty or still has a template
        const current = editor.getValue().trim();
        const isTemplate = Object.values(templates).some(
            (t) => t.trim() === current
        );
        if (!current || isTemplate) {
            editor.setValue(templates[lang] || "", -1);
        }
    }

    if (langSelect) {
        langSelect.addEventListener("change", function () {
            setLanguage(this.value);
        });
        // Set initial mode
        setLanguage(langSelect.value);
    }

    // ── Form submission — copy editor content into hidden textarea ──
    const form = document.getElementById("submit-form");
    const hiddenCode = document.getElementById("hidden-code");

    if (form && hiddenCode) {
        form.addEventListener("submit", function () {
            hiddenCode.value = editor.getValue();
        });
    }

    // ── Copy buttons for sample I/O ─────────────────────────────────
    document.querySelectorAll(".copy-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            const targetId = this.getAttribute("data-target");
            const target = document.getElementById(targetId);
            if (!target) return;

            navigator.clipboard
                .writeText(target.textContent)
                .then(function () {
                    btn.textContent = "✓";
                    setTimeout(function () {
                        btn.textContent = "Copy";
                    }, 1500);
                })
                .catch(function () {
                    // Fallback for older browsers
                    const range = document.createRange();
                    range.selectNodeContents(target);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                    document.execCommand("copy");
                    sel.removeAllRanges();
                    btn.textContent = "✓";
                    setTimeout(function () {
                        btn.textContent = "Copy";
                    }, 1500);
                });
        });
    });

    // ── Flash message close buttons ─────────────────────────────────
    document.querySelectorAll(".flash-close").forEach(function (btn) {
        btn.addEventListener("click", function () {
            this.closest(".flash").remove();
        });
    });
});
