(() => {
    "use strict";

    const HEART_SELECTOR = ".heart-rating__input";
    const FLASH_CLOSE_SELECTOR = "[data-flash-close]";
    const SUBMIT_BUTTON_SELECTOR = "[data-submit-button]";

    /**
     * ♡評価
     * 選択した位置まで白くする
     */
    function updateHeartGroup(groupName, selectedValue) {
        const inputs = document.querySelectorAll(
            `input[name="${CSS.escape(groupName)}"]${HEART_SELECTOR}`
        );

        inputs.forEach((input) => {
            const value = Number(input.value);
            input.classList.toggle("is-filled", value <= selectedValue);
        });

        const output = document.querySelector(
            `[data-score-output="${CSS.escape(groupName)}"]`
        );

        if (output) {
            output.textContent = `${selectedValue} / 5`;
        }
    }

    function initializeHeartRatings() {
        const heartInputs = document.querySelectorAll(HEART_SELECTOR);

        heartInputs.forEach((input) => {

            input.addEventListener("change", () => {

                updateHeartGroup(
                    input.name,
                    Number(input.value)
                );

                if ("vibrate" in navigator) {
                    navigator.vibrate(18);
                }

            });

            if (input.checked) {
                updateHeartGroup(
                    input.name,
                    Number(input.value)
                );
            }

        });
    }

    /**
     * 通知
     */
    function initializeFlashMessages() {

        document.querySelectorAll(FLASH_CLOSE_SELECTOR)
            .forEach((button) => {

                button.addEventListener("click", () => {

                    const message =
                        button.closest(".flash-message");

                    if (!message) return;

                    message.style.opacity = "0";
                    message.style.transform = "translateY(-6px)";

                    setTimeout(() => {
                        message.remove();
                    }, 180);

                });

            });

        document.querySelectorAll(".flash-message")
            .forEach((message) => {

                setTimeout(() => {

                    if (!message.isConnected) return;

                    message.style.opacity = "0";
                    message.style.transform = "translateY(-6px)";

                    setTimeout(() => {
                        message.remove();
                    }, 180);

                }, 5000);

            });

    }

    /**
     * 二重送信防止
     */
    function initializeSubmitButtons() {

        document.querySelectorAll("form")
            .forEach((form) => {

                form.addEventListener("submit", () => {

                    const button =
                        form.querySelector(SUBMIT_BUTTON_SELECTOR);

                    if (!button) return;

                    button.disabled = true;
                    button.dataset.originalText = button.textContent;
                    button.textContent = "保存中…";

                });

            });

    }

    /**
     * 電車ログ
     */
    function initializeTrainForm() {

        const form =
            document.querySelector("[data-train-form]");

        if (!form) return;

        const resultInputs =
            form.querySelectorAll('input[name="result"]');

        const optionalNames = [
            "travel_support",
            "station_count",
            "crowd_level",
            "panic_flag",
            "failed_stage",
            "before_state",
            "during_state",
            "after_state"
        ];

        const optionalGroups = optionalNames
            .map(name => {

                const field =
                    form.querySelector(`[name="${name}"]`);

                return field
                    ? field.closest(".field-group")
                    : null;

            })
            .filter(Boolean);

        function updateTrainFields() {

            const selected =
                form.querySelector(
                    'input[name="result"]:checked'
                );

            const noPlan =
                selected?.value === "no_plan";

            optionalGroups.forEach(group => {
                group.hidden = noPlan;
            });

            if (noPlan) {

                optionalNames.forEach(name => {

                    form.querySelectorAll(`[name="${name}"]`)
                        .forEach(field => {

                            if (field instanceof HTMLInputElement) {

                                if (
                                    field.type === "checkbox" ||
                                    field.type === "radio"
                                ) {
                                    field.checked = false;
                                } else {
                                    field.value = "";
                                }

                            }

                            if (
                                field instanceof HTMLTextAreaElement ||
                                field instanceof HTMLSelectElement
                            ) {
                                field.value = "";
                            }

                        });

                });

            }

        }

        resultInputs.forEach(input => {
            input.addEventListener(
                "change",
                updateTrainFields
            );
        });

        updateTrainFields();

    }

    /**
     * textarea自動拡張
     */
    function initializeAutoResize() {

        const textareas =
            document.querySelectorAll(".text-area");

        function resize(textarea) {

            textarea.style.height = "auto";

            textarea.style.height =
                Math.max(
                    textarea.scrollHeight,
                    110
                ) + "px";

        }

        textareas.forEach(textarea => {

            textarea.addEventListener(
                "input",
                () => resize(textarea)
            );

            resize(textarea);

        });

    }

    /**
     * AI診察レポート全文をコピーします。
     */
    function initializeReportCopyButtons() {
        document.querySelectorAll("[data-copy-report]").forEach((button) => {
            button.addEventListener("click", async () => {
                const card = button.closest(".report-card");
                const report = card?.querySelector("[data-report-text]");
                const text = report?.textContent?.trim();
                if (!text) return;

                const originalText = button.textContent.trim();

                try {
                    await navigator.clipboard.writeText(text);
                } catch (error) {
                    const textArea = document.createElement("textarea");
                    textArea.value = text;
                    textArea.style.position = "fixed";
                    textArea.style.opacity = "0";
                    document.body.appendChild(textArea);
                    textArea.select();
                    document.execCommand("copy");
                    textArea.remove();
                }

                button.textContent = "コピーしました";
                window.setTimeout(() => {
                    button.textContent = originalText;
                }, 1800);
            });
        });
    }

    /**
     * Service Worker
     */
    function registerServiceWorker() {

        if (!("serviceWorker" in navigator))
            return;

        window.addEventListener("load", async () => {

            try {

                await navigator.serviceWorker.register(
                    "/static/sw.js"
                );

            } catch (err) {

                console.warn(err);

            }

        });

    }

    /**
     * 初期化
     */
    function initialize() {

        initializeHeartRatings();
        initializeFlashMessages();
        initializeSubmitButtons();
        initializeTrainForm();
        initializeAutoResize();
        initializeReportCopyButtons();
        registerServiceWorker();

    }

    if (document.readyState === "loading") {

        document.addEventListener(
            "DOMContentLoaded",
            initialize
        );

    } else {

        initialize();

    }

})();