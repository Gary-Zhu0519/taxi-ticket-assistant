(function () {
    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function renderRowsTable(rows) {
        if (!rows || !rows.length) {
            return "";
        }
        const columns = Object.keys(rows[0]);
        const headHtml = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
        const bodyHtml = rows
            .map((row) => {
                const cells = columns.map((column) => `<td>${escapeHtml(row[column] ?? "-")}</td>`).join("");
                return `<tr>${cells}</tr>`;
            })
            .join("");
        return `
            <div class="assistant-table-wrap">
                <div class="assistant-block-title">执行结果</div>
                <div class="table-responsive">
                    <table class="table table-sm align-middle assistant-result-table mb-0">
                        <thead><tr>${headHtml}</tr></thead>
                        <tbody>${bodyHtml}</tbody>
                    </table>
                </div>
            </div>
        `;
    }

    function renderRiskFlags(riskFlags) {
        if (!riskFlags || !riskFlags.length) {
            return "";
        }
        return `
            <div class="assistant-risk-box">
                <div class="assistant-risk-title">风险提示</div>
                <div class="assistant-risk-list">
                    ${riskFlags.map((flag) => `<span class="assistant-risk-flag">${escapeHtml(flag)}</span>`).join("")}
                </div>
            </div>
        `;
    }

    function renderMeta(payload) {
        const segments = [];
        if (payload.intent) {
            segments.push(`意图：${escapeHtml(payload.intent)}`);
        }
        if (typeof payload.data_count === "number") {
            segments.push(`结果数：${escapeHtml(payload.data_count)}`);
        }
        return segments.length ? `<div class="assistant-message-meta">${segments.join(" / ")}</div>` : "";
    }

    function renderOperationType(payload) {
        const operationType = payload.operation_type;
        if (!operationType) {
            return "";
        }
        const label = payload.operation_type_label || operationType;
        const cssClass = operationType === "write" ? "assistant-op-chip is-write" : "assistant-op-chip is-query";
        return `
            <div class="assistant-op-row">
                <span class="${cssClass}">operation_type: ${escapeHtml(label)}</span>
            </div>
        `;
    }

    function renderCommands(commands, executedSteps) {
        if (!commands || !commands.length) {
            return "";
        }
        const stepMap = new Map((executedSteps || []).map((item) => [item.step_title, item]));
        const cards = commands
            .map((command, index) => {
                const executed = stepMap.get(command.step_title) || {};
                const filters = command.filters || {};
                const filterEntries = Object.keys(filters).length
                    ? Object.entries(filters)
                          .map(([key, value]) => `<span class="assistant-filter-chip">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
                          .join("")
                    : `<span class="assistant-filter-chip is-empty">无参数</span>`;
                return `
                    <div class="assistant-command-card">
                        <div class="assistant-command-head">
                            <span class="assistant-command-index">Step ${index + 1}</span>
                            <span class="assistant-command-tool">${escapeHtml(command.tool || "-")}</span>
                        </div>
                        <div class="assistant-command-title">${escapeHtml(command.step_title || "执行命令")}</div>
                        <div class="assistant-command-line">intent: <code>${escapeHtml(command.intent || "-")}</code></div>
                        <div class="assistant-command-line">payload / filters: ${filterEntries}</div>
                        <div class="assistant-command-line text-secondary">返回记录：${escapeHtml(executed.data_count ?? 0)}</div>
                    </div>
                `;
            })
            .join("");
        return `
            <div class="assistant-command-box">
                <div class="assistant-block-title">执行命令</div>
                <div class="assistant-command-list">${cards}</div>
            </div>
        `;
    }

    function renderSqlDebug(sqlDebug) {
        if (!sqlDebug || !sqlDebug.length) {
            return "";
        }
        const items = sqlDebug
            .map((entry, index) => {
                const params = entry.parameters === null || entry.parameters === undefined
                    ? "None"
                    : escapeHtml(JSON.stringify(entry.parameters, null, 2));
                return `
                    <div class="assistant-sql-item">
                        <div class="assistant-sql-head">
                            <span class="assistant-sql-index">SQL ${index + 1}</span>
                            <span class="assistant-sql-kind">${entry.executemany ? "executemany" : "execute"}</span>
                        </div>
                        <pre class="assistant-sql-code">${escapeHtml(entry.statement || "")}</pre>
                        <div class="assistant-sql-params-title">parameters</div>
                        <pre class="assistant-sql-params">${params}</pre>
                    </div>
                `;
            })
            .join("");
        return `
            <div class="assistant-sql-box">
                <div class="assistant-block-title">数据库命令（Debug）</div>
                <div class="assistant-sql-list">${items}</div>
            </div>
        `;
    }

    function createAssistantApp(mode) {
        const app = document.getElementById(`assistant-${mode}-app`);
        if (!app) {
            return;
        }

        const enabled = app.dataset.enabled === "true";
        const apiUrl = app.dataset.apiUrl;
        const form = app.querySelector(`form[data-mode="${mode}"]`);
        const input = document.getElementById(`assistant-${mode}-input`);
        const sendBtn = document.getElementById(`assistant-${mode}-send-btn`);
        const chatWindow = document.getElementById(`assistant-${mode}-chat-window`);
        const errorBox = document.getElementById(`assistant-${mode}-error-box`);
        const labels = {
            unified: {
                loading: "正在识别操作类型、规划执行链路并校验权限，请稍候...",
                idle: "发送请求",
                empty: "请输入要执行的查询或业务操作。",
            },
            query: {
                loading: "正在分析问题、规划查询步骤并按权限范围检索数据，请稍候...",
                idle: "发送查询",
                empty: "请输入要查询的问题。",
            },
            action: {
                loading: "正在识别业务动作、校验权限并调用受控后台服务，请稍候...",
                idle: "执行操作",
                empty: "请输入要执行的业务操作。",
            },
        };
        const modeLabel = labels[mode] || labels.unified;

        function setLoadingState(loading) {
            sendBtn.disabled = loading || !enabled;
            input.disabled = loading || !enabled;
            sendBtn.textContent = loading ? "处理中..." : modeLabel.idle;
        }

        function showError(message) {
            if (!message) {
                errorBox.classList.add("d-none");
                errorBox.textContent = "";
                return;
            }
            errorBox.classList.remove("d-none");
            errorBox.textContent = message;
        }

        function appendMessage(position, text, payload = {}) {
            const wrapper = document.createElement("div");
            wrapper.className = `assistant-message ${position === "right" ? "assistant-message-right" : "assistant-message-left"}`;
            const bubble = document.createElement("div");
            bubble.className = `assistant-bubble ${position === "right" ? "assistant-bubble-user" : "assistant-bubble-assistant"}`;
            bubble.innerHTML = `
                <div class="assistant-message-text">${escapeHtml(text)}</div>
                ${position === "left" ? renderOperationType(payload) : ""}
                ${position === "left" ? renderMeta(payload) : ""}
                ${position === "left" ? renderCommands(payload.commands, payload.executed_steps) : ""}
                ${position === "left" && payload.debug_mode ? renderSqlDebug(payload.sql_debug) : ""}
                ${position === "left" ? renderRiskFlags(payload.risk_flags) : ""}
                ${position === "left" ? renderRowsTable(payload.rows) : ""}
            `;
            wrapper.appendChild(bubble);
            chatWindow.appendChild(wrapper);
            chatWindow.scrollTop = chatWindow.scrollHeight;
            return wrapper;
        }

        async function sendMessage(message) {
            const text = String(message || "").trim();
            if (!enabled) {
                showError("未配置可用的大模型服务，当前智能助手暂不可用。");
                return;
            }
            if (!text) {
                showError(modeLabel.empty);
                return;
            }

            showError("");
            appendMessage("right", text);
            input.value = "";
            setLoadingState(true);
            const loadingNode = appendMessage("left", modeLabel.loading);

            try {
                const response = await fetch(apiUrl, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ message: text }),
                });
                const data = await response.json();
                loadingNode.remove();

                if (!response.ok || !data.ok) {
                    appendMessage("left", data.message || "请求失败，请稍后重试。", {
                        intent: data.error_code || "error",
                        data_count: 0,
                        rows: [],
                        risk_flags: [],
                        commands: data.commands || [],
                        executed_steps: data.executed_steps || [],
                    });
                    return;
                }

                appendMessage("left", data.answer || "已完成请求。", data);
            } catch (error) {
                loadingNode.remove();
                showError("请求失败，请检查 Flask 服务是否正常运行。");
                appendMessage("left", "请求失败，请稍后重试。", {
                    intent: "error",
                    data_count: 0,
                    rows: [],
                    risk_flags: [],
                    commands: [],
                    executed_steps: [],
                });
            } finally {
                setLoadingState(false);
                input.focus();
            }
        }

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            sendMessage(input.value);
        });

        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendMessage(input.value);
            }
        });

        document.querySelectorAll(`.assistant-example-btn[data-target="${mode}"]`).forEach((button) => {
            button.addEventListener("click", function () {
                const example = button.dataset.example || "";
                input.value = example;
                sendMessage(example);
            });
        });
    }

    function initWorkflow(container) {
        const steps = JSON.parse(container.dataset.steps || "[]");
        if (!steps.length) {
            return;
        }

        const title = container.dataset.workflowTitle || "Workflow";
        const rail = document.createElement("div");
        rail.className = "assistant-workflow-rail";

        const detail = document.createElement("div");
        detail.className = "assistant-workflow-detail";

        function activate(stepId) {
            const current = steps.find((item) => item.id === stepId) || steps[0];
            rail.querySelectorAll(".assistant-workflow-node").forEach((node) => {
                node.classList.toggle("is-active", node.dataset.stepId === current.id);
            });
            detail.innerHTML = `
                <div class="assistant-workflow-detail-title">${escapeHtml(title)} / ${escapeHtml(current.title)}</div>
                <div class="assistant-workflow-detail-text">${escapeHtml(current.desc)}</div>
            `;
        }

        steps.forEach((step, index) => {
            const node = document.createElement("button");
            node.type = "button";
            node.className = "assistant-workflow-node";
            node.dataset.stepId = step.id;
            node.innerHTML = `
                <span class="assistant-workflow-index">${index + 1}</span>
                <span class="assistant-workflow-node-title">${escapeHtml(step.title)}</span>
            `;
            node.addEventListener("click", function () {
                activate(step.id);
            });
            rail.appendChild(node);

            if (index < steps.length - 1) {
                const arrow = document.createElement("div");
                arrow.className = "assistant-workflow-arrow";
                arrow.textContent = "→";
                rail.appendChild(arrow);
            }
        });

        container.appendChild(rail);
        container.appendChild(detail);
        activate(steps[0].id);
    }

    createAssistantApp("unified");
    document.querySelectorAll(".assistant-workflow").forEach(initWorkflow);
})();
