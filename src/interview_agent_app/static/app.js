const state = {
  sessionId: null,
  busy: false,
  finished: false,
  currentRound: 0,
  totalRounds: 3,
  startedAt: null,
  timerId: null,
  timerPaused: false,
  elapsedBeforePause: 0,
};
const topicsByTarget = window.INTERVIEW_TOPICS || {};

const $ = (id) => document.getElementById(id);
const log = $("log");
const customSelects = new Map();

function setTarget(target) {
  $("language").value = target;
  document.querySelectorAll(".preset").forEach((button) => {
    button.classList.toggle("active", button.dataset.target === target);
  });
  refreshTopics(target);
  syncStaticLabels();
}

function refreshTopics(target) {
  const current = $("topic").value;
  const topics = topicsByTarget[target] || ["综合"];
  $("topic").innerHTML = "";
  topics.forEach((topic) => {
    const option = document.createElement("option");
    option.value = topic;
    option.textContent = topic;
    $("topic").appendChild(option);
  });
  $("topic").value = topics.includes(current) ? current : topics[0];
  syncCustomSelect("topic");
}

function setBusy(value, text) {
  state.busy = value;
  $("status").textContent = text || (value ? "正在请求模型..." : "就绪");
  updateControls();
}

function isTrainingActive() {
  return Boolean(state.sessionId) && !state.finished;
}

function updateControls() {
  const active = isTrainingActive();
  $("startBtn").disabled = state.busy;
  $("memoryBtn").disabled = state.busy;
  $("progressBtn").disabled = state.busy;
  $("wrongBookBtn").disabled = state.busy;
  $("historyBtn").disabled = state.busy;
  $("wrongBtn").disabled = state.busy;
  $("resetBtn").disabled = state.busy || !state.sessionId;
  $("pauseBtn").disabled = !state.startedAt;
  $("solution").disabled = state.busy || !active;
  $("submitBtn").disabled = state.busy || !active;
  $("hintBtn").disabled = state.busy || !active || $("mode").value === "mock";
  $("answerBtn").disabled = state.busy || !active || $("mode").value === "mock";
  $("nextBtn").disabled = state.busy || !active;
  $("draftBtn").disabled = !active;
  document.querySelectorAll("[data-hint]").forEach((button) => {
    button.disabled = state.busy || !active || $("mode").value === "mock";
  });
  syncProgress();
  syncStaticLabels();
  syncAllCustomSelects();
}

function initCustomSelects() {
  document.querySelectorAll("select.setting-native").forEach((select) => {
    const wrapper = document.createElement("div");
    wrapper.className = "custom-select";
    wrapper.dataset.selectFor = select.id;

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "custom-select-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");

    const value = document.createElement("span");
    value.className = "custom-select-value";
    const chevron = document.createElement("span");
    chevron.className = "custom-select-chevron";
    chevron.textContent = "⌄";
    trigger.append(value, chevron);

    const menu = document.createElement("div");
    menu.className = "custom-select-menu";
    menu.setAttribute("role", "listbox");

    wrapper.append(trigger, menu);
    select.insertAdjacentElement("afterend", wrapper);
    customSelects.set(select.id, { select, wrapper, trigger, value, menu });

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleCustomSelect(select.id);
    });
    select.addEventListener("change", () => syncCustomSelect(select.id));
    syncCustomSelect(select.id);
  });

  document.addEventListener("click", closeCustomSelects);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeCustomSelects();
  });
}

function toggleCustomSelect(id) {
  const item = customSelects.get(id);
  if (!item) return;
  const nextOpen = !item.wrapper.classList.contains("open");
  closeCustomSelects();
  item.wrapper.classList.toggle("open", nextOpen);
  item.trigger.setAttribute("aria-expanded", String(nextOpen));
}

function closeCustomSelects() {
  customSelects.forEach(({ wrapper, trigger }) => {
    wrapper.classList.remove("open");
    trigger.setAttribute("aria-expanded", "false");
  });
}

function syncCustomSelect(id) {
  const item = customSelects.get(id);
  if (!item) return;
  const { select, value, menu } = item;
  const selected = select.selectedOptions[0];
  value.textContent = selected?.textContent || select.value || "请选择";
  menu.innerHTML = "";
  Array.from(select.options).forEach((option) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "custom-select-option";
    button.textContent = option.textContent;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(option.value === select.value));
    button.classList.toggle("active", option.value === select.value);
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      select.value = option.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      closeCustomSelects();
    });
    menu.appendChild(button);
  });
}

function syncAllCustomSelects() {
  customSelects.forEach((_, id) => syncCustomSelect(id));
}

function normalizeTemperatureInput(showMessage = false) {
  const input = $("temperature");
  const raw = input.value.trim();
  input.setCustomValidity("");
  if (!raw) {
    if (showMessage) input.setCustomValidity("温度必须是 0 到 2 之间的数字。");
    return false;
  }

  const value = Number(raw);
  if (!Number.isFinite(value)) {
    input.setCustomValidity("温度必须是 0 到 2 之间的数字。");
    return false;
  }

  const clamped = Math.max(0, Math.min(2, value));
  if (clamped !== value) {
    input.value = String(clamped);
  }
  return true;
}

function syncStaticLabels() {
  const language = $("language").value || "Python";
  const difficulty = $("difficulty").value || "medium";
  const topic = $("topic").value || "综合";
  $("sessionTitle").textContent = `${topic}编程面试 · ${formatDifficulty(difficulty)}`;
  $("editorTitle").textContent = `代码编写（${language}）`;
  $("problemMeta").textContent = `${language} · ${formatDifficulty(difficulty)}`;
  $("userName").textContent = $("operator").value || "default";
  $("userRole").textContent = `${language} 开发工程师`;
}

function formatDifficulty(value) {
  const map = { easy: "Easy", medium: "Medium", hard: "Hard" };
  return map[String(value).toLowerCase()] || value;
}

function syncProgress() {
  const total = Number($("rounds").value) || state.totalRounds || 3;
  const current = state.currentRound || 0;
  const percent = total ? Math.max(0, Math.min(1, current / total)) : 0;
  $("progressText").textContent = `${current}/${total}`;
  $("progressRing").style.background = `conic-gradient(var(--accent) ${percent * 360}deg, #eef2f6 0deg)`;
  $("progressDetail").innerHTML = current
    ? `已完成 ${current} 题<br />共 ${total} 题`
    : `尚未开始<br />共 ${total} 题`;
}

function startTimer() {
  state.startedAt = Date.now();
  state.elapsedBeforePause = 0;
  state.timerPaused = false;
  clearInterval(state.timerId);
  state.timerId = setInterval(renderTimer, 1000);
  renderTimer();
}

function stopTimer() {
  clearInterval(state.timerId);
  state.timerId = null;
  state.startedAt = null;
  state.elapsedBeforePause = 0;
  state.timerPaused = false;
  $("pauseBtn").textContent = "Ⅱ";
}

function toggleTimer() {
  if (!state.startedAt) return;
  if (state.timerPaused) {
    state.startedAt = Date.now() - state.elapsedBeforePause;
    state.timerPaused = false;
    state.timerId = setInterval(renderTimer, 1000);
    $("pauseBtn").textContent = "Ⅱ";
  } else {
    state.elapsedBeforePause = Date.now() - state.startedAt;
    state.timerPaused = true;
    clearInterval(state.timerId);
    $("pauseBtn").textContent = "▶";
  }
  renderTimer();
}

function renderTimer() {
  const elapsed = state.startedAt ? (state.timerPaused ? state.elapsedBeforePause : Date.now() - state.startedAt) : 0;
  const totalSeconds = Math.floor(elapsed / 1000);
  const hours = String(Math.floor(totalSeconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((totalSeconds % 3600) / 60)).padStart(2, "0");
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  $("timer").textContent = `${hours}:${minutes}:${seconds}`;
}

function clearLog() {
  log.innerHTML = "";
  $("emptyState").hidden = false;
  $("problemView").hidden = true;
  $("hintCard").classList.remove("visible");
  $("problemBody").innerHTML = "";
  $("hintBody").innerHTML = "";
}

function addMessage(title, content, meta) {
  if (isProblemTitle(title)) {
    showProblem(title, content);
    return;
  }
  if (title === "提示") {
    showHint(content);
    return;
  }
  appendLogMessage(title, content, meta);
}

function isProblemTitle(title) {
  return /^第\s*\d+\s*题$/.test(String(title || ""));
}

function showProblem(title, content) {
  $("emptyState").hidden = true;
  $("problemView").hidden = false;
  $("problemTitle").textContent = state.currentRound && state.totalRounds
    ? `第 ${state.currentRound} / ${state.totalRounds} 题`
    : title;
  $("problemBody").innerHTML = "";
  $("problemBody").appendChild(renderMessageBody(content));
  $("hintCard").classList.remove("visible");
  $("hintBody").innerHTML = "";
}

function showHint(content) {
  $("hintBody").innerHTML = "";
  $("hintBody").appendChild(renderMessageBody(content));
  $("hintCard").classList.add("visible");
}

function appendLogMessage(title, content, meta) {
  const article = document.createElement("article");
  article.className = "message";
  const header = document.createElement("header");
  const heading = document.createElement("span");
  heading.textContent = title;
  const info = document.createElement("span");
  info.className = "small";
  info.textContent = meta || currentMetaText();
  const metadata = parseEvaluationJson(content);
  const body = renderMessageBody(content);
  header.append(heading, info);
  article.append(header);
  if (metadata) {
    article.appendChild(renderScoreCard(metadata));
  }
  article.append(body);
  log.appendChild(article);
  article.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function currentMetaText() {
  return `${$("operator").value || "default"} · ${$("language").value || "Python"} · ${$("topic").value || "综合"} · ${$("mode").value || "practice"} · ${$("model").value || ""}`;
}

function renderMessageBody(content) {
  const body = document.createElement("div");
  body.className = "message-body";
  const text = cleanDisplayText(stripEvaluationJson(content));
  if (!text) {
    const paragraph = document.createElement("p");
    paragraph.textContent = "暂无内容。";
    body.appendChild(paragraph);
    return body;
  }

  let codeLines = [];
  let inCode = false;
  text.split(/\r?\n/).forEach((rawLine) => {
    const line = rawLine.trimEnd();
    if (line.trim().startsWith("```")) {
      if (inCode) {
        appendCodeBlock(body, codeLines.join("\n"));
        codeLines = [];
      }
      inCode = !inCode;
      return;
    }
    if (inCode) {
      codeLines.push(rawLine);
      return;
    }
    appendFormattedLine(body, line);
  });
  if (codeLines.length) {
    appendCodeBlock(body, codeLines.join("\n"));
  }
  return body;
}

function cleanDisplayText(content) {
  return String(content || "")
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/<\/?think>/gi, "")
    .replace(/^\s*---+\s*$/gm, "")
    .trim();
}

function appendFormattedLine(parent, line) {
  const text = line.trim();
  if (!text) return;

  const headingMatch = text.match(/^#{1,6}\s+(.+)$/);
  if (headingMatch) {
    const heading = document.createElement("h3");
    appendInlineText(heading, headingMatch[1]);
    parent.appendChild(heading);
    return;
  }

  const listMatch = text.match(/^(?:[-*+]|\d+[.)])\s+(.+)$/);
  const element = document.createElement(listMatch ? "div" : "p");
  if (listMatch) {
    element.className = "list-item";
  }
  appendInlineText(element, listMatch ? listMatch[1] : text);
  parent.appendChild(element);
}

function appendInlineText(parent, text) {
  const parts = String(text || "").split(/(\*\*[^*]+\*\*)/g);
  parts.forEach((part) => {
    if (!part) return;
    if (part.startsWith("**") && part.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = part.slice(2, -2);
      parent.appendChild(strong);
    } else {
      parent.appendChild(document.createTextNode(part));
    }
  });
}

function appendCodeBlock(parent, code) {
  const pre = document.createElement("pre");
  pre.className = "code-block";
  pre.textContent = code.trim();
  parent.appendChild(pre);
}

function parseEvaluationJson(content) {
  const match = String(content || "").match(/```evaluation_json\s*(\{[\s\S]*?\})\s*```/);
  if (!match) return null;
  try {
    return JSON.parse(match[1]);
  } catch {
    return null;
  }
}

function stripEvaluationJson(content) {
  return String(content || "").replace(/```evaluation_json\s*\{[\s\S]*?\}\s*```/g, "").trim();
}

function clampScore(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(100, number));
}

function renderScoreCard(metadata) {
  const card = document.createElement("div");
  card.className = "score-card";
  const score = clampScore(metadata.score);
  const maxScore = metadata.max_score || 100;
  const main = document.createElement("div");
  main.className = "score-main";
  const left = document.createElement("div");
  left.innerHTML = `<span class="small">本题评分</span><strong>${score ?? "无"}</strong><span class="small"> / ${maxScore}</span>`;
  const verdict = document.createElement("span");
  verdict.className = "small";
  verdict.textContent = metadata.is_wrong_question ? "已进入错题关注" : "表现可继续推进";
  main.append(left, verdict);
  card.appendChild(main);

  const bars = document.createElement("div");
  bars.className = "score-bars";
  [
    ["正确性", metadata.correctness],
    ["复杂度", metadata.complexity],
    ["边界条件", metadata.edge_cases],
    ["代码质量", metadata.code_quality],
    ["沟通表达", metadata.communication],
  ].forEach(([label, value]) => {
    const itemScore = clampScore(value);
    if (itemScore == null) return;
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `<span>${label}</span><div class="bar"><span style="width:${itemScore}%"></span></div><span>${itemScore}</span>`;
    bars.appendChild(row);
  });
  card.appendChild(bars);

  const tags = Array.isArray(metadata.tags) ? metadata.tags : [];
  if (tags.length) {
    const tagBox = document.createElement("div");
    tagBox.className = "tags";
    tags.slice(0, 10).forEach((tag) => {
      const item = document.createElement("span");
      item.className = "tag";
      item.textContent = tag;
      tagBox.appendChild(item);
    });
    card.appendChild(tagBox);
  }
  return card;
}

async function post(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json();
  if (!response.ok || data.error) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function applyResponse(data) {
  if (data.session_id) state.sessionId = data.session_id;
  if (data.round) state.currentRound = data.round;
  if (data.total) state.totalRounds = data.total;
  state.finished = Boolean(data.finished);
  $("status").textContent = data.status || "就绪";
  if (data.title && data.content) {
    addMessage(data.title, data.content, data.meta);
  }
  if (data.memory_context) {
    addMessage("已载入历史 Memory", data.memory_context, "SQLite 知识库");
  }
  if (data.review_context) {
    addMessage("已载入错题上下文", data.review_context, "SQLite 知识库");
  }
  if (data.finished) {
    $("solution").value = "";
    updateLineNumbers();
    stopTimer();
  }
  updateControls();
}

async function startSession() {
  if (!normalizeTemperatureInput(true)) {
    $("temperature").reportValidity();
    return;
  }
  clearLog();
  state.sessionId = null;
  state.finished = false;
  state.currentRound = 0;
  state.totalRounds = Number($("rounds").value) || 3;
  stopTimer();
  setBusy(true, "正在生成第一题...");
  try {
    const data = await post("/api/start", {
      api_key: $("apiKey").value,
      base_url: $("baseUrl").value,
      model: $("model").value,
      operator: $("operator").value,
      language: $("language").value,
      topic: $("topic").value,
      mode: $("mode").value,
      difficulty: $("difficulty").value,
      rounds: $("rounds").value,
      temperature: $("temperature").value,
    });
    $("solution").value = "";
    updateLineNumbers();
    startTimer();
    applyResponse(data);
  } catch (error) {
    addMessage("错误", error.message);
    state.sessionId = null;
    state.finished = false;
    stopTimer();
  } finally {
    setBusy(false, state.sessionId ? "第一题已生成。" : "启动失败。");
  }
}

async function showMemory() {
  setBusy(true, "正在读取 SQLite memory...");
  try {
    const data = await post("/api/memory", {
      operator: $("operator").value,
      language: $("language").value,
    });
    addMessage("历史 Memory", data.content || "当前方向还没有历史 memory。", "SQLite 知识库");
  } catch (error) {
    addMessage("错误", error.message);
  } finally {
    setBusy(false, "就绪");
  }
}

async function showHistory() {
  setBusy(true, "正在读取训练历史...");
  try {
    const data = await post("/api/history", {
      operator: $("operator").value,
    });
    renderHistory(data.sessions || []);
  } catch (error) {
    addMessage("错误", error.message);
  } finally {
    setBusy(false, "就绪");
  }
}

async function showProgress() {
  setBusy(true, "正在读取训练进度...");
  try {
    const data = await post("/api/progress", {
      operator: $("operator").value,
    });
    renderProgress(data);
  } catch (error) {
    addMessage("错误", error.message);
  } finally {
    setBusy(false, "就绪");
  }
}

function renderProgress(data) {
  const overview = data.overview || {};
  const article = document.createElement("article");
  article.className = "message";
  const header = document.createElement("header");
  const heading = document.createElement("span");
  heading.textContent = "训练进度";
  const info = document.createElement("span");
  info.className = "small";
  info.textContent = "SQLite 知识库";
  header.append(heading, info);
  const stats = document.createElement("div");
  stats.className = "stats-grid";
  [
    ["训练次数", overview.session_count ?? 0],
    ["评估次数", overview.evaluation_count ?? 0],
    ["平均分", overview.avg_score ?? "无"],
    ["最高分", overview.best_score ?? "无"],
  ].forEach(([label, value]) => {
    const item = document.createElement("div");
    item.className = "stat";
    item.innerHTML = `<strong>${value}</strong><span>${label}</span>`;
    stats.appendChild(item);
  });
  const body = document.createElement("pre");
  const weak = (data.weak_tags || []).map((item) => `${item.tag} x${item.count}`).join("、") || "暂无";
  const byTarget = (data.by_target || []).map((item) => `${item.target}: ${item.avg_score ?? "无"} 分 / ${item.count} 次`).join("\n") || "暂无";
  const recent = (data.recent || []).map((item) => `${item.day}: ${item.avg_score ?? "无"} 分 / ${item.count} 次`).join("\n") || "暂无";
  body.textContent = `薄弱点排行：${weak}\n\n方向表现：\n${byTarget}\n\n最近训练：\n${recent}`;
  article.append(header, stats, body);
  log.appendChild(article);
  log.scrollTop = log.scrollHeight;
}

async function showWrongBook() {
  setBusy(true, "正在读取错题本...");
  try {
    const data = await post("/api/wrong_questions", {
      operator: $("operator").value,
      language: $("language").value,
    });
    renderWrongBook(data.items || []);
  } catch (error) {
    addMessage("错误", error.message);
  } finally {
    setBusy(false, "就绪");
  }
}

function renderWrongBook(items) {
  const wrapper = document.createElement("div");
  wrapper.className = "history-list";
  if (!items.length) {
    addMessage("错题本", "当前方向还没有低分评估记录。", "SQLite 知识库");
    return;
  }
  items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "history-item";
    const title = document.createElement("strong");
    title.textContent = `${item.target || ""} · ${item.topic || "综合"} · 第 ${item.round_index || "-"} 题 · ${item.score ?? "无"} 分`;
    const meta = document.createElement("span");
    meta.className = "small";
    let tags = [];
    try { tags = JSON.parse(item.tags || "[]"); } catch {}
    meta.textContent = `${item.created_at || ""} · ${tags.join("、") || "无标签"}`;
    const preview = document.createElement("pre");
    preview.textContent = stripEvaluationJson(String(item.content || "")).slice(0, 600);
    const actions = document.createElement("div");
    actions.className = "history-actions";
    const retryBtn = document.createElement("button");
    retryBtn.textContent = "重练相似题";
    retryBtn.addEventListener("click", () => startWrongReview(item));
    const detailBtn = document.createElement("button");
    detailBtn.textContent = "查看原复盘";
    detailBtn.addEventListener("click", () => showSessionDetail(item.session_id));
    actions.append(retryBtn, detailBtn);
    card.append(title, meta, preview, actions);
    wrapper.appendChild(card);
  });
  const article = document.createElement("article");
  article.className = "message";
  const header = document.createElement("header");
  const heading = document.createElement("span");
  heading.textContent = "错题本";
  const info = document.createElement("span");
  info.className = "small";
  info.textContent = "低于 70 分或未评分的评估";
  header.append(heading, info);
  article.append(header, wrapper);
  log.appendChild(article);
  log.scrollTop = log.scrollHeight;
}

function renderHistory(sessions) {
  const wrapper = document.createElement("div");
  wrapper.className = "history-list";
  if (!sessions.length) {
    addMessage("训练历史", "当前操作者还没有训练记录。", "SQLite 知识库");
    return;
  }
  sessions.forEach((item) => {
    const card = document.createElement("div");
    card.className = "history-item";
    const title = document.createElement("strong");
    title.textContent = `${item.target || ""} · ${item.topic || "综合"} · ${item.mode || "practice"}`;
    const meta = document.createElement("span");
    meta.className = "small";
    meta.textContent = `${item.started_at || ""} · 平均分 ${item.avg_score ?? "无"} · 评估 ${item.evaluation_count || 0} 次`;
    const actions = document.createElement("div");
    actions.className = "history-actions";
    const detailBtn = document.createElement("button");
    detailBtn.textContent = "查看复盘";
    detailBtn.addEventListener("click", () => showSessionDetail(item.id));
    const retryBtn = document.createElement("button");
    retryBtn.textContent = "错题重练";
    retryBtn.addEventListener("click", () => startWrongReview(item));
    actions.append(detailBtn, retryBtn);
    card.append(title, meta, actions);
    wrapper.appendChild(card);
  });
  const article = document.createElement("article");
  article.className = "message";
  const header = document.createElement("header");
  const heading = document.createElement("span");
  heading.textContent = "训练历史";
  const info = document.createElement("span");
  info.className = "small";
  info.textContent = "SQLite 知识库";
  header.append(heading, info);
  article.append(header, wrapper);
  log.appendChild(article);
  log.scrollTop = log.scrollHeight;
}

async function showSessionDetail(sessionId) {
  setBusy(true, "正在生成复盘...");
  try {
    const data = await post("/api/session_detail", { session_id: sessionId });
    addMessage("训练复盘", formatSessionDetail(data), "SQLite 知识库");
  } catch (error) {
    addMessage("错误", error.message);
  } finally {
    setBusy(false, "就绪");
  }
}

function formatSessionDetail(data) {
  const session = data.session || {};
  const events = data.events || [];
  const lines = [
    `方向：${session.target || ""}`,
    `题型：${session.topic || "综合"}`,
    `模式：${session.mode || "practice"}`,
    `难度：${session.difficulty || ""}`,
    `开始：${session.started_at || ""}`,
    "",
  ];
  events.forEach((event) => {
    const score = event.score == null ? "" : ` score=${event.score}`;
    lines.push(`[${event.round_index || "-"}] ${event.kind}${score}`);
    lines.push(String(event.content || "").slice(0, 1200));
    lines.push("");
  });
  return lines.join("\n");
}

async function startWrongReview(source) {
  if (!normalizeTemperatureInput(true)) {
    $("temperature").reportValidity();
    return;
  }
  setTarget(source?.target || $("language").value);
  const desiredTopic = source?.topic || "综合";
  if ([...$("topic").options].some((option) => option.value === desiredTopic)) {
    $("topic").value = desiredTopic;
    syncCustomSelect("topic");
  }
  $("mode").value = "review";
  syncCustomSelect("mode");
  clearLog();
  state.sessionId = null;
  state.finished = false;
  state.currentRound = 0;
  state.totalRounds = Number($("rounds").value) || 3;
  stopTimer();
  setBusy(true, "正在生成错题重练...");
  try {
    const data = await post("/api/start", {
      api_key: $("apiKey").value,
      base_url: $("baseUrl").value,
      model: $("model").value,
      operator: $("operator").value,
      language: $("language").value,
      topic: $("topic").value,
      mode: "review",
      difficulty: $("difficulty").value,
      rounds: $("rounds").value,
      temperature: $("temperature").value,
      retry_source_session_id: source?.id || "",
    });
    $("solution").value = "";
    updateLineNumbers();
    startTimer();
    applyResponse(data);
  } catch (error) {
    addMessage("错误", error.message);
    stopTimer();
  } finally {
    setBusy(false, state.sessionId ? "错题重练已开始。" : "启动失败。");
  }
}

async function sendAction(action) {
  if (!state.sessionId) return;
  const labels = {
    submit: "正在评估答案...",
    hint: "正在生成提示...",
    answer: "正在生成参考答案...",
    next: "正在进入下一题...",
    reset: "正在结束训练...",
  };
  setBusy(true, labels[action]);
  try {
    const solution = $("solution").value.trim();
    if (action === "submit" && !solution) {
      throw new Error("请先输入答案。");
    }
    const data = await post("/api/action", {
      session_id: state.sessionId,
      action,
      solution,
    });
    if (action === "submit" || action === "next") {
      $("solution").value = "";
      updateLineNumbers();
    }
    if (action === "reset") {
      state.sessionId = null;
      state.finished = true;
      stopTimer();
    }
    applyResponse(data);
  } catch (error) {
    addMessage("错误", error.message);
  } finally {
    setBusy(false, state.finished ? "训练已结束。" : "就绪");
  }
}

function saveDraft() {
  const key = `interview-agent-draft:${$("operator").value || "default"}:${$("language").value || "Python"}`;
  localStorage.setItem(key, $("solution").value);
  setBusy(false, "草稿已保存到当前浏览器。");
}

function restoreDraft() {
  const key = `interview-agent-draft:${$("operator").value || "default"}:${$("language").value || "Python"}`;
  const draft = localStorage.getItem(key);
  if (draft && !$("solution").value) {
    $("solution").value = draft;
    updateLineNumbers();
  }
}

function updateLineNumbers() {
  const count = Math.max(1, $("solution").value.split(/\r?\n/).length);
  $("lineNumbers").textContent = Array.from({ length: count }, (_, index) => index + 1).join("\n");
}

function toggleHintCard() {
  const body = $("hintBody");
  const hidden = body.hidden;
  body.hidden = !hidden;
  $("hintToggle").textContent = hidden ? "⌃" : "⌄";
}

$("startBtn").addEventListener("click", startSession);
$("memoryBtn").addEventListener("click", showMemory);
$("progressBtn").addEventListener("click", showProgress);
$("wrongBookBtn").addEventListener("click", showWrongBook);
$("historyBtn").addEventListener("click", showHistory);
$("wrongBtn").addEventListener("click", () => startWrongReview(null));
$("resetBtn").addEventListener("click", () => sendAction("reset"));
$("submitBtn").addEventListener("click", () => sendAction("submit"));
$("hintBtn").addEventListener("click", () => sendAction("hint"));
$("answerBtn").addEventListener("click", () => sendAction("answer"));
$("nextBtn").addEventListener("click", () => sendAction("next"));
$("pauseBtn").addEventListener("click", toggleTimer);
$("draftBtn").addEventListener("click", saveDraft);
$("hintToggle").addEventListener("click", toggleHintCard);
$("solution").addEventListener("input", updateLineNumbers);
$("operator").addEventListener("input", syncStaticLabels);
$("difficulty").addEventListener("change", updateControls);
$("topic").addEventListener("change", updateControls);
$("rounds").addEventListener("input", updateControls);
$("mode").addEventListener("change", updateControls);
$("temperature").addEventListener("input", () => normalizeTemperatureInput(false));
$("temperature").addEventListener("blur", () => normalizeTemperatureInput(true));
document.querySelectorAll("[data-hint]").forEach((button) => {
  button.addEventListener("click", () => sendAction("hint"));
});
document.querySelectorAll(".preset").forEach((button) => {
  button.addEventListener("click", () => setTarget(button.dataset.target));
});
initCustomSelects();
restoreDraft();
updateLineNumbers();
syncProgress();
syncStaticLabels();
updateControls();
