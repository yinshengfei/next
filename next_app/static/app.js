"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const form = $("#preferences");
const findButton = $("#find-button");
const originalFindLabel = findButton.textContent;
let chosenTime = 30;
let energy = "medium";
let busy = false;
let cards = [];

function preferences() {
  return {
    minutes: chosenTime === "custom" ? Number($("#custom-minutes").value) : chosenTime,
    energy,
  };
}

function clearResults() {
  if (!cards.length) return;
  cards = [];
  $("#results").hidden = true;
  $("#empty-state").hidden = false;
  $("#status").textContent = "Find a new action for your updated time and energy.";
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

function setBusy(value, finding = false) {
  busy = value;
  document.querySelectorAll("button").forEach((button) => { button.disabled = value; });
  $("#custom-minutes").disabled = value || chosenTime !== "custom";
  form.setAttribute("aria-busy", String(value));
  findButton.classList.toggle("loading", value && finding);
  findButton.textContent = value && finding ? "Finding your next action…" : originalFindLabel;
}

function showError(message) {
  $("#error").textContent = message;
  $("#error").hidden = false;
  $("#status").textContent = "";
}

async function post(path, payload) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 100000);
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
