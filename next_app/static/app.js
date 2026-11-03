"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const form = $("#preferences");
const findButton = $("#find-button");
const originalFindLabel = findButton.textContent;
let chosenTime = 30;
let energy = "medium";
let busy = false;
let cards = [];
let overviewLoaded = false;
let overviewLoading = false;
let overviewSelected = "today";
let overviewUpdatePending = false;
let overviewUpdateTimer = null;
let overviewData = null;
const chatTimezoneSelect = $("#chat-timezone");
const savedChatTimezone = localStorage.getItem("next-chat-timezone");
if (["America/Los_Angeles", "Asia/Shanghai"].includes(savedChatTimezone)) chatTimezoneSelect.value = savedChatTimezone;
const chatTimezone = () => chatTimezoneSelect.value;
const chatTimezoneLabel = () => chatTimezone() === "Asia/Shanghai" ? "上海时间" : "洛杉矶时间";
chatTimezoneSelect.addEventListener("change", () => {
  localStorage.setItem("next-chat-timezone", chatTimezone());
  refreshChat().catch((error) => showChatError(error.message));
  if (overviewData) showOverview(overviewData);
});

function scheduleOverviewUpdate() {
  overviewUpdatePending = true;
  clearTimeout(overviewUpdateTimer);
  overviewUpdateTimer = setTimeout(() => {
    if (busy || overviewLoading) { scheduleOverviewUpdate(); return; }
    overviewUpdatePending = false;
    loadOverview(true);
  }, 800);
}

function renderOverviewTask(task, finished) {
  const row = node("article", undefined, "overview-task");
  const link = node("a", task.name, "overview-task-link");
  link.href = `https://app.todoist.com/app/task/${encodeURIComponent(task.id)}`;
  link.target = "_blank"; link.rel = "noopener noreferrer";
  row.append(link);
  const date = task.completed_at ? `完成于 ${task.completed_at.slice(0, 10)}`
    : [`安排 ${task.due || "未设置"}`, `截止 ${task.deadline || "未设置"}`].join(" · ");
  row.append(node("span", date, "overview-task-date"));
  if (!finished) {
    const details = [`P${5 - task.priority}`, task.duration_minutes ? `${task.duration_minutes} 分钟` : null, task.project || "Inbox"].filter(Boolean);
    row.append(node("span", details.join(" · "), "overview-task-date"));
    const edit = node("button", "编辑", "text-button overview-edit");
    edit.type = "button";
    edit.addEventListener("click", () => openOverviewDialog(task));
    row.append(edit);
  }
  if (task.description) row.append(node("p", task.description, "overview-recorded"));
  else if (!finished) row.append(node("p", "Progress not recorded in Todoist", "overview-unrecorded"));
  if (task.attention) row.append(node("p", `Note · ${task.attention}`, "overview-attention"));
  return row;
}

let editingOverviewTask = null;
const overviewDialog = $("#overview-dialog");
const overviewTaskForm = $("#overview-task-form");

async function openOverviewDialog(task = null) {
  editingOverviewTask = task;
  overviewTaskForm.reset();
  $("#overview-dialog-error").hidden = true;
  $("#overview-dialog-title").textContent = task ? "编辑事项" : "新建事项";
  $("#overview-delete").hidden = !task;
  const projectSelect = overviewTaskForm.elements.project_id;
  if (!projectOptions.length) {
    try { await loadProjects(); } catch (error) { showError(error.message); return; }
  }
  projectSelect.replaceChildren(new Option("Inbox", ""), ...projectOptions.map((project) => new Option(project.name, project.id)));
  if (task) {
    overviewTaskForm.elements.content.value = task.name;
    overviewTaskForm.elements.description.value = task.description || "";
    overviewTaskForm.elements.due_date.value = task.due?.slice(0, 10) || "";
    overviewTaskForm.elements.deadline_date.value = task.deadline?.slice(0, 10) || "";
    overviewTaskForm.elements.duration_minutes.value = task.duration_minutes || "";
    overviewTaskForm.elements.priority.value = String(task.priority);
    projectSelect.value = task.project_id || "";
  }
  overviewDialog.showModal();
  overviewTaskForm.elements.content.focus();
}

function overviewFormPayload() {
  const fields = overviewTaskForm.elements;
  return { content: fields.content.value.trim(), description: fields.description.value,
    project_id: fields.project_id.value || null, due_date: fields.due_date.value || null,
    deadline_date: fields.deadline_date.value || null, priority: Number(fields.priority.value),
    duration_minutes: fields.duration_minutes.value ? Number(fields.duration_minutes.value) : null };
}

function overviewDialogError(message) {
  $("#overview-dialog-error").textContent = message;
  $("#overview-dialog-error").hidden = false;
}

$("#overview-add").addEventListener("click", () => openOverviewDialog());
$("#overview-cancel").addEventListener("click", () => overviewDialog.close());
overviewTaskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!overviewTaskForm.reportValidity()) return;
  const payload = overviewFormPayload();
  if (payload.due_date && payload.deadline_date && payload.due_date > payload.deadline_date) {
    overviewDialogError("安排日期不能晚于截止日期。"); return;
  }
  const submit = overviewTaskForm.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    if (editingOverviewTask) await post(`/api/overview/task/${encodeURIComponent(editingOverviewTask.id)}/edit`,
      { ...payload, expected: editingOverviewTask.snapshot });
    else await post("/api/overview/task", payload);
    overviewDialog.close();
    if (overviewLoading) scheduleOverviewUpdate(); else await loadOverview(true);
  } catch (error) { overviewDialogError(error.message); }
  finally { submit.disabled = false; }
});
$("#overview-delete").addEventListener("click", async () => {
  const task = editingOverviewTask;
  if (!task || !window.confirm(`确定从 Todoist 删除“${task.name}”？`)) return;
  $("#overview-delete").disabled = true;
  try {
    await post(`/api/overview/task/${encodeURIComponent(task.id)}/delete`, task.snapshot);
    overviewDialog.close();
    if (overviewLoading) scheduleOverviewUpdate(); else await loadOverview(true);
  } catch (error) { overviewDialogError(error.message); }
  finally { $("#overview-delete").disabled = false; }
});

function renderOverviewSection(section) {
  const wrapper = node("section", undefined, "overview-section");
  wrapper.dataset.overviewView = section.key;
  wrapper.hidden = section.key !== overviewSelected;
  const header = node("div", undefined, "overview-section-heading");
  header.append(node("h2", section.title), node("span", `${section.count} task${section.count === 1 ? "" : "s"}`, "hint"));
  wrapper.append(header);
  const rules = { today: "今天到期及已逾期待办", week: "截至本周日到期的待办，包含逾期事项",
    all: "全部未完成待办，按领域和任务组组织", finished: "过去 7 天的已完成记录" };
  wrapper.append(node("p", rules[section.key], "hint"));
  if (!section.areas.length) {
    wrapper.append(node("p", section.key === "finished" ? "Nothing finished in the last seven days." : "No tasks here right now.", "hint"));
    return wrapper;
  }
  section.areas.forEach((area) => {
    const areaNode = node("div", undefined, "overview-area");
    areaNode.append(node("h3", area.title, "overview-area-title"));
    const grid = node("div", undefined, "overview-grid");
    area.workstreams.forEach((group) => {
    const card = node("article", undefined, `overview-group ${group.emphasis}`);
    card.append(node("h4", group.title));
    const total = group.progress.finished + group.progress.open;
    const label = `${group.progress.finished} finished · ${group.progress.open} open`;
    card.append(node("p", label, "overview-progress-label"));
    const track = node("progress", undefined, "overview-progress-track");
    track.max = Math.max(total, 1); track.value = group.progress.finished;
    track.setAttribute("aria-label", `${group.title}: ${label}`);
    card.append(track);
    const list = node("div", undefined, "overview-task-list");
    group.tasks.forEach((task) => list.append(renderOverviewTask(task, section.key === "finished")));
    card.append(list); grid.append(card);
    });
    areaNode.append(grid); wrapper.append(areaNode);
  });
  return wrapper;
}

function showOverview(data) {
  overviewData = data;
  $("#overview-sections").replaceChildren(...data.sections.map(renderOverviewSection));
  const buttons = data.sections.map((section) => {
    const button = node("button", `${section.title} · ${section.count}`, "choice");
    button.type = "button"; button.dataset.overviewKey = section.key;
    button.setAttribute("aria-pressed", String(section.key === overviewSelected));
    button.addEventListener("click", () => {
      overviewSelected = section.key;
      document.querySelectorAll("[data-overview-view]").forEach((view) => { view.hidden = view.dataset.overviewView !== section.key; });
      document.querySelectorAll("[data-overview-key]").forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.overviewKey === section.key)));
    });
    return button;
  });
  $("#overview-views").replaceChildren(...buttons);
  const since = data.history_since ? `完成记录自 ${data.history_since}` : "完成历史尚未同步";
  const syncTime = data.last_sync ? new Date(data.last_sync).toLocaleString("zh-CN", { timeZone: chatTimezone() }) : "尚未同步";
  $("#overview-meta").textContent = `${data.active_count} 条未完成待办 · ${since} · 进度条按待办数量计算，不代表工作量百分比 · 最后同步 ${syncTime}`;
  if (data.layout_mode !== "ai" && data.active_count) {
    $("#overview-error").textContent = "AI 分组尚未完成。下方保留全部待办，已通过校验的分组会在修复完成后更新。";
    $("#overview-error").hidden = false;
  } else { $("#overview-error").hidden = true; }
}

async function loadOverview(refresh = false) {
  if (busy || overviewLoading) return;
  overviewLoading = true;
  overviewLoaded = true;
  $("#overview-status").textContent = refresh ? "Syncing Todoist and updating workstreams…" : "Loading your overview…";
  $("#overview-error").hidden = true;
  $("#overview-refresh").disabled = true;
  try {
    const response = await fetch("/api/overview/cached");
    if (response.ok) {
      const cached = await response.json();
      if (cached.available) {
        showOverview(cached);
        $("#overview-status").textContent = "已载入本地 Overview，正在检查更新…";
      }
    }
    const data = await post("/api/overview", { refresh }, 300000);
    showOverview(data);
    $("#overview-status").textContent = data.sync_error ? `Showing cached tasks. Sync issue: ${data.sync_error}` : "";
    $("#chat-sync").textContent = data.sync_error ? "Todoist 同步未成功，Overview 仍显示上次记录。切回窗口会自动重试。"
      : data.layout_mode !== "ai" ? "待办已同步，AI 分组更新尚未完成；All 中仍显示所有待办。"
      : "Overview 已自动更新 · 无日期事项请在 All 查看";
  } catch (error) {
    $("#overview-error").textContent = error.message; $("#overview-error").hidden = false;
    $("#overview-status").textContent = "";
    $("#chat-sync").textContent = "Overview 更新未完成，创建结果请以操作卡片为准。切回窗口会自动重试。";
  } finally { overviewLoading = false; $("#overview-refresh").disabled = false; }
}

$("#overview-refresh").addEventListener("click", () => loadOverview(true));
window.addEventListener("focus", scheduleOverviewUpdate);
document.addEventListener("visibilitychange", () => { if (!document.hidden) scheduleOverviewUpdate(); });
setInterval(() => { if (!document.hidden) scheduleOverviewUpdate(); }, 300000);

function preferences() {
  return {
    minutes: chosenTime === "custom" ? Number($("#custom-minutes").value) : chosenTime,
    energy,
    description: $("#recommend-description").value.trim(),
  };
}

function clearResults() {
  if (!cards.length) return;
  cards = [];
  $("#results").hidden = true;
  $("#empty-state").hidden = false;
  $("#status").textContent = "条件已更新，请重新获取推荐。";
  $("#error").hidden = true;
}

$("#time-choices").addEventListener("click", (event) => {
  const button = event.target.closest("[data-minutes]");
  if (!button || busy) return;
  chosenTime = button.dataset.minutes === "custom" ? "custom" : Number(button.dataset.minutes);
  $("#time-choices").querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  $("#custom-wrap").hidden = chosenTime !== "custom";
  $("#custom-minutes").disabled = chosenTime !== "custom";
  if (chosenTime === "custom") $("#custom-minutes").focus();
  clearResults();
});
$("#energy-choices").addEventListener("click", (event) => {
  const button = event.target.closest("[data-energy]");
  if (!button || busy) return;
  energy = button.dataset.energy;
  $("#energy-choices").querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  clearResults();
});
$("#custom-minutes").addEventListener("input", clearResults);
$("#recommend-description").addEventListener("input", clearResults);

function setBusy(value, finding = false) {
  busy = value;
  document.querySelectorAll("button").forEach((button) => { button.disabled = value; });
  $("#custom-minutes").disabled = value || chosenTime !== "custom";
  $("#recommend-description").disabled = value;
  form.setAttribute("aria-busy", String(value));
  findButton.classList.toggle("loading", value && finding);
  findButton.textContent = value && finding ? "Finding your next action…" : originalFindLabel;
}

function showError(message) {
  $("#error").textContent = message;
  $("#error").hidden = false;
  $("#status").textContent = "";
}

async function post(path, payload, timeoutMs = 100000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Next-Local": "1" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request failed. Please try again.");
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("This request took too long. Please try again.");
    if (error instanceof TypeError || error instanceof SyntaxError) throw new Error("Cannot reach Next. Make sure the app is still running in Terminal.");
    throw error;
  } finally {
    clearTimeout(timer);
    if (["/api/chat/confirm", "/api/chat/action", "/api/recommend/complete", "/api/recommend/progress/confirm"].includes(path)
        && payload.decision !== "cancel") {
      $("#chat-sync").textContent = "正在核对 Todoist 并更新 Overview…";
      scheduleOverviewUpdate();
    }
  }
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function addAssumptions(container, assumptions) {
  if (!assumptions.length) return;
  const wrapper = node("div", undefined, "assumptions");
  wrapper.append(node("span", "ASSUMPTIONS", "step-label"));
  const list = node("ul");
  assumptions.forEach((assumption) => list.append(node("li", assumption)));
  wrapper.append(list);
  container.append(wrapper);
}

function previewProgress(card, article, plan) {
  const panel = $(".progress-preview", article);
  panel.replaceChildren(node("p", plan.reply));
  const form = node("form", undefined, "progress-confirm-form");
  const title = document.createElement("input");
  title.type = "text"; title.maxLength = 500; title.value = plan.updated_title ?? card.task_name;
  form.append(labeledField("Task title", title));
  const description = document.createElement("textarea");
  description.rows = 3; description.maxLength = 3000;
  description.value = plan.updated_description ?? plan.current_description ?? "";
  form.append(labeledField("Updated task description", description));
  const complete = document.createElement("input");
  complete.type = "checkbox"; complete.checked = plan.complete;
  form.append(labeledField("Mark the whole task complete", complete));
  const newRows = [];
  plan.new_tasks.forEach((draft) => {
    const row = node("div", undefined, "progress-new-task");
    const selected = document.createElement("input"); selected.type = "checkbox"; selected.checked = true;
    const name = document.createElement("input"); name.type = "text"; name.maxLength = 500; name.value = draft.content;
    const due = document.createElement("input"); due.type = "date"; due.value = draft.due_date || "";
    row.append(labeledField("Create new task", selected), labeledField("Title", name), labeledField("Due", due));
    form.append(row);
    newRows.push({ selected, name, due, draft });
  });
  const submit = node("button", "Confirm changes in Todoist", "primary-button");
  submit.type = "submit"; form.append(submit); panel.append(form); panel.hidden = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if (busy) return;
    const tasks = newRows.filter((row) => row.selected.checked).map((row) => ({
      ...row.draft, content: row.name.value.trim(), due_date: row.due.value || null,
    }));
    if (tasks.some((task) => !task.content) || !title.value.trim()) return showError("Every selected task needs a title.");
    setBusy(true); $("#error").hidden = true;
    try {
      const result = await post("/api/recommend/progress/confirm", {
        proposal_id: plan.proposal_id,
        updated_title: title.value.trim() === card.task_name ? null : title.value.trim(),
        updated_description: description.value === (plan.current_description ?? "") ? null : description.value,
        complete: complete.checked, new_tasks: tasks,
      }, 180000);
      if (result.completed) article.replaceWith(node("p", `Completed in Todoist: ${card.task_name}`, "status"));
      else panel.replaceChildren(node("p", `Saved to Todoist. ${result.created_task_ids.length} new task(s) created.`));
      $("#status").textContent = "Progress saved. Find a new action to refresh the list.";
    } catch (error) { showError(error.message); }
    finally { setBusy(false); }
  });
}

async function describeProgress(card, article, event) {
  event.preventDefault(); if (busy) return;
  const progress = $(".progress-input", article).value.trim();
  if (!progress) return;
  setBusy(true); $("#error").hidden = true;
  $("#status").textContent = "Preparing changes for your review…";
  try {
    const plan = await post("/api/recommend/progress", { recommendation_id: card.recommendation_id, progress });
    previewProgress(card, article, plan); $("#status").textContent = "Review the changes before saving.";
  } catch (error) { showError(error.message); }
  finally { setBusy(false); }
}

async function completeCard(card, article) {
  if (busy) return;
  setBusy(true); $("#error").hidden = true;
  try {
    await post("/api/recommend/complete", { recommendation_id: card.recommendation_id });
    article.replaceWith(node("p", `Completed in Todoist: ${card.task_name}`, "status"));
    $("#status").textContent = "Task completed. Find a new action to refresh recommendations.";
  } catch (error) { showError(error.message); }
  finally { setBusy(false); }
}

function renderCard(card) {
  const fragment = $("#recommendation-template").content.cloneNode(true);
  const article = $("article", fragment);
  $(".project", article).textContent = card.project_name || "Todoist";
  $(".duration", article).textContent = `About ${card.estimated_minutes} min`;
  const link = $(".task-link", article);
  link.textContent = card.task_name;
  link.href = `https://app.todoist.com/app/task/${encodeURIComponent(card.task_id)}`;
  $(".action-title", article).textContent = card.action;
  $(".why", article).textContent = card.why;
  const dates = [];
  if (card.deadline) dates.push(`Deadline ${card.deadline}`);
  if (card.due) dates.push(`Due ${card.due}`);
  $(".task-date", article).textContent = dates.join(" · ");
  $(".first-step p", article).textContent = card.first_step;
  if (card.assumptions.length) {
    const assumptions = $(".assumptions", article);
    assumptions.hidden = false;
    card.assumptions.forEach((text) => $("ul", assumptions).append(node("li", text)));
  }
  $(".easier-button", article).addEventListener("click", () => simplify(card, article));
  $(".complete-button", article).addEventListener("click", () => completeCard(card, article));
  $(".progress-form", article).addEventListener("submit", (event) => describeProgress(card, article, event));
  return fragment;
}

async function findAction(exclude = false) {
  if (busy || !form.reportValidity()) return;
  const selected = preferences();
  const excludeIds = exclude ? cards.map((card) => card.task_id) : [];
  setBusy(true, true);
  $("#error").hidden = true;
  $("#status").textContent = "Reading Todoist and finding a useful place to start…";
  try {
    const data = await post("/api/recommend", { ...selected, exclude_ids: excludeIds });
    // Keep the current choices if there are no further alternatives.
    if (!data.recommendations.length) {
      $("#status").textContent = data.message;
      if (!exclude) {
        cards = [];
        $("#results").hidden = true;
        $("#empty-state").hidden = false;
      }
      return;
    }
    cards = data.recommendations;
    $("#primary-result").replaceChildren(renderCard(cards[0]));
    $("#alternatives").replaceChildren(...cards.slice(1).map(renderCard));
    $("#alternatives-section").hidden = cards.length < 2;
    $("#result-context").textContent = `${selected.minutes} min · ${selected.energy} energy`;
    $("#candidate-note").textContent = `${data.candidate_count} candidates from ${data.total_tasks} active tasks · Times are estimates`;
    $("#results").hidden = false;
    $("#empty-state").hidden = true;
    $("#status").textContent = "";
    $("#results-title").focus({ preventScroll: true });
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function simplify(card, article) {
  if (busy) return;
  const button = $(".easier-button", article);
  const originalLabel = button.textContent;
  setBusy(true);
  button.textContent = "Making it easier…";
  $("#error").hidden = true;
  $("#status").textContent = "Finding a smaller first step…";
  try {
    const data = await post("/api/easier", { ...preferences(), recommendation_id: card.recommendation_id });
    const panel = $(".breakdown", article);
    const list = node("ol");
    data.steps.forEach((step) => {
      const item = node("li", step.action);
      item.append(node("span", `About ${step.estimated_minutes} min`, "hint"));
      list.append(item);
    });
    panel.replaceChildren(node("h4", "Just the next small steps"), list);
    addAssumptions(panel, data.assumptions);
    panel.hidden = false;
    button.textContent = "✧ Try smaller steps";
    $("#status").textContent = "";
  } catch (error) {
    button.textContent = originalLabel;
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

form.addEventListener("submit", (event) => { event.preventDefault(); findAction(); });
$("#alternatives-button").addEventListener("click", () => findAction(true));

async function checkSetup() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error();
    const data = await response.json();
    $("#connection").textContent = data.configured ? "Local · Configured" : "Local · Setup needed";
    $("#setup-note").hidden = data.configured;
    if (!data.configured) $("#setup-message").textContent = data.message;
  } catch {
    showError("Cannot check setup. Make sure Next is running and refresh the page.");
  }
}
checkSetup();

// Chat can answer general questions and prepare tasks; only this explicit action writes.
let projectOptions = [];
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  const chat = button.dataset.mode === "chat";
  const overview = button.dataset.mode === "overview";
  document.documentElement.classList.toggle("chat-mode", chat);
  $("#overview-panel").hidden = !overview;
  $("#chat-panel").hidden = !chat;
  $("#next-panel").hidden = overview || chat;
  if (overview) loadOverview();
  if (chat) $("#chat-messages").scrollTop = $("#chat-messages").scrollHeight;
  document.querySelectorAll("[data-mode]").forEach((item) => {
    if (item === button) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
}));
loadOverview();

function renderMessage(message) {
  const article = node("article", undefined, `chat-message ${message.role}`);
  article.append(node("span", message.role === "user" ? "You" : "Next", "step-label"));
  const time = node("time", new Intl.DateTimeFormat("zh-CN", { timeZone: chatTimezone(), hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(message.created_at)), "message-time");
  time.dateTime = message.created_at;
  time.title = `${new Date(message.created_at).toLocaleString("zh-CN", { timeZone: chatTimezone() })} · ${chatTimezoneLabel()}`;
  article.append(time);
  article.append(node("p", message.content));
  return article;
}

function labeledField(label, control) {
  const wrapper = node("label", undefined, "draft-field");
  wrapper.append(node("span", label), control);
  return wrapper;
}

async function decideActions(actions, decision = "confirm") {
  if (busy) return;
  const undated = actions.filter((action) => action.operation === "create" && !action.due_date);
  if (decision === "confirm" && undated.length && !window.confirm(`以下事项没有日期，将显示在 All，不进入 Today / This Week：\n${undated.map((a) => a.content).join("\n")}\n\n确认按无日期创建？`)) return;
  setBusy(true); $("#chat-error").hidden = true;
  let applied = 0;
  try {
    for (const action of actions) {
      $("#chat-status").textContent = `正在处理 ${applied + 1} / ${actions.length}…`;
      if (action.operation === "create") {
        if (decision === "cancel") {
          await post("/api/chat/draft/cancel", { id: action.id, decision: "cancel" });
        } else {
          const { id, content, description, project_id, due_date, deadline_date, duration_minutes, priority } = action;
          const result = await post("/api/chat/confirm", { drafts: [{ id, content, description, project_id, due_date, deadline_date, duration_minutes, priority }] });
          if (result.results[0].state !== "created") throw new Error(result.results[0].message || "新建未确认，请检查事项状态。");
        }
      } else {
        await post("/api/chat/action", { id: action.id, decision });
      }
      applied += 1;
    }
    $("#chat-status").textContent = decision === "confirm" ? `已同步 ${applied} 项操作` : "已取消";
  } catch (error) {
    showChatError(`已处理 ${applied} 项，其余尚未确认。${error.message}`);
  } finally {
    overviewLoaded = false;
    try { await refreshChat(); } catch (error) { showChatError(error.message); }
    setBusy(false);
  }
}

function renderChatAction(action) {
  if (action.operation === "create") action = { ...action, before: { name: action.content },
    reason: action.description || "新建待办", changes: {}, state: action.state === "created" ? "applied" : action.state };
  const row = node("article", undefined, "action-row");
  const details = node("details", undefined, "action-detail");
  details.open = action.state === "pending";
  const summary = node("summary");
  summary.append(node("span", action.operation === "complete" ? "完成" : action.operation === "create" ? "新建" : "修改", "action-tag"), node("span", action.before.name, "action-name"));
  if (action.operation === "create") summary.append(node("span", action.due_date || "无日期 · All", "action-state"));
  details.append(summary);
  const body = node("div", undefined, "action-expanded");
  body.append(node("p", action.reason));
  const link = node("a", "在 Todoist 查看 ↗");
  link.href = `https://app.todoist.com/app/task/${encodeURIComponent(action.task_id)}`;
  link.target = "_blank"; link.rel = "noopener noreferrer";
  if (action.task_id) body.append(link);
  if (action.operation === "create") {
    const project = projectOptions.find((item) => item.id === action.project_id)?.name || (action.project_id || "Inbox");
    [["事项", action.content], ["安排日期", action.due_date || "未设置"],
      ["截止日期", action.deadline_date || "未设置"], ["备注", action.description || "未设置"],
      ["项目", project], ["优先级", `P${5 - action.priority}`],
      ["预计时长", action.duration_minutes ? `${action.duration_minutes} 分钟` : "未设置"]]
      .forEach(([label, value]) => body.append(node("p", `${label}：${value}`)));
  }
  const fields = { content: ["标题", "name"], description: ["备注", "description"], due_date: ["安排日期", "due"], deadline_date: ["截止日期", "deadline"], priority: ["优先级", "priority"], due_string: ["安排日期", "due"], duration: ["预计时长", "duration_minutes"] };
  if (action.operation === "complete") body.append(node("p", "未完成 → 已完成"));
  if (action.operation !== "create") {
    const before = action.before;
    [["当前事项", before.name], ["当前安排日期", before.due || "未设置"],
      ["当前截止日期", before.deadline || "未设置"], ["当前备注", before.description || "未设置"],
      ["当前项目", before.project_name || "Inbox"], ["当前优先级", `P${5 - before.priority}`],
      ["当前预计时长", before.duration_minutes ? `${before.duration_minutes} 分钟` : "未设置"]]
      .forEach(([label, value]) => body.append(node("p", `${label}：${value}`)));
  }
  Object.entries(action.changes).forEach(([key, value]) => {
    if (key === "duration_unit") return;
    const [label, source] = fields[key] || [key, key];
    const before = key === "priority" ? `P${5 - action.before.priority}` : action.before[source] || "未设置";
    const after = key === "priority" ? `P${5 - value}` : key === "due_string" ? "无日期" : value || "清空";
    body.append(node("p", `${label}：${before} → ${after}`));
  });
  details.append(body); row.append(details);
  const controls = node("div", undefined, "action-controls");
  if (action.state === "pending") {
    const confirm = node("button", "Confirm", "compact-confirm");
    const cancel = node("button", "×", "compact-cancel");
    cancel.setAttribute("aria-label", `取消：${action.before.name}`);
    confirm.type = cancel.type = "button";
    confirm.addEventListener("click", () => decideActions([action]));
    cancel.addEventListener("click", () => decideActions([action], "cancel"));
    controls.append(confirm, cancel);
  } else {
    const states = { applied: "✓ 已同步", cancelled: "已取消", stale: "需重新预览", uncertain: "需核对", applying: "处理中" };
    controls.append(node("span", states[action.state] || action.state, "action-state"));
    if (action.task_id && action.state === "applied") {
      const view = node("a", "查看 ↗", "action-state");
      view.href = link.href; view.target = "_blank"; view.rel = "noopener noreferrer";
      controls.append(view);
    }
  }
  row.append(controls);
  if (action.error) row.append(node("p", action.error, "error-inline action-row-error"));
  return row;
}

function renderActionGroup(actions) {
  const group = node("section", undefined, "action-group");
  const header = node("div", undefined, "action-group-header");
  const pending = actions.filter((action) => action.state === "pending");
  header.append(node("span", `事项操作 · ${actions.length} 项`, "action-group-label"));
  if (pending.length) {
    const all = node("button", `Confirm all · ${pending.length}`, "compact-confirm");
    all.type = "button";
    all.addEventListener("click", () => decideActions(pending));
    header.append(all);
  }
  group.append(header, ...actions.map(renderChatAction));
  return group;
}

async function refreshChat() {
  const response = await fetch("/api/chat/history");
  if (!response.ok) throw new Error("Cannot load local chat history.");
  const data = await response.json();
  const messages = $("#chat-messages");
  let previousHour = "";
  messages.replaceChildren(...data.messages.flatMap((message) => {
    const hour = new Intl.DateTimeFormat("sv-SE", { timeZone: chatTimezone(), year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" }).format(new Date(message.created_at));
    const separator = hour !== previousHour ? node("div", `${hour}:00 · ${chatTimezoneLabel()}`, "chat-hour") : null;
    previousHour = hour;
    const item = renderMessage(message);
    const actions = [
      ...(data.actions || []).filter((action) => action.message_id === message.id),
      ...data.drafts.filter((draft) => draft.message_id === message.id).map((draft) => ({ ...draft, operation: "create" }))
    ];
    if (actions.length) item.append(renderActionGroup(actions));
    return separator ? [separator, item] : [item];
  }));
  messages.scrollTop = messages.scrollHeight;

}

async function loadProjects() {
  const response = await fetch("/api/projects");
  if (!response.ok) throw new Error("Cannot load Todoist projects. Check setup and refresh.");
  projectOptions = (await response.json()).projects;
}

function showChatError(message) {
  $("#chat-error").textContent = message;
  $("#chat-error").hidden = false;
  $("#chat-status").textContent = "";
}

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !$("#chat-form").reportValidity()) return;
  const message = $("#chat-input").value.trim();
  if (!message) return;
  setBusy(true);
  $("#chat-error").hidden = true;
  $("#chat-status").textContent = "Thinking and checking your Todoist tasks…";
  try {
    let legacyBackend = false;
    try {
      await post("/api/chat", { message, timezone: chatTimezone() });
    } catch (error) {
      // A running older backend rejects the newly added timezone field before saving the message.
      if (error.message !== "Please check the submitted fields and try again.") throw error;
      await post("/api/chat", { message });
      legacyBackend = true;
    }
    $("#chat-input").value = "";
    await refreshChat();
    $("#chat-status").textContent = legacyBackend ? "消息已发送；重启 Next 后 Chat 才会按所选时区理解日期。" : "";
  } catch (error) {
    showChatError(error.message);
  } finally {
    setBusy(false);
  }
});

$("#clear-chat").addEventListener("click", async () => {
  if (busy || !window.confirm("Delete the chat history and all drafts stored on this Mac? Created Todoist tasks stay in Todoist.")) return;
  setBusy(true);
  try {
    await post("/api/chat/clear", {});
    await refreshChat();
    $("#chat-status").textContent = "Local chat history cleared.";
    $("#chat-error").hidden = true;
  } catch (error) {
    showChatError(error.message);
  } finally {
    setBusy(false);
  }
});

(async () => {
  try {
    await loadProjects();
    await refreshChat();
  } catch (error) {
    showChatError(error.message);
  }
})();
