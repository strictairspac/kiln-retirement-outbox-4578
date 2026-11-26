import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { resultTable } from "../components/common/table.js";
import { receiptsPanel } from "../components/receipts.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, download, time } from "../utils/format.js";

function reviewForm(id, saved) {
  const form = el("form", {id: "review-form"},
    field("复核意见", el("textarea", {name: "note", required: true, minlength: 3, maxlength: 500, rows: 3})),
    errorText(), el("button", {type: "submit"}, "签署复核"));
  submit(form, async values => saved(await api.submitReview(id, {note: values.get("note")})));
  return form;
}

function signature(review) {
  return el("div", {"data-testid": "reviewed", class: "signature"},
    el("strong", {}, `已签署 · ${review.actor}`), el("p", {}, review.note), el("time", {}, time(review.created_at)));
}

export async function evidencePage(config, user, route) {
  const runs = (await api.runs()).filter(item => item.status !== "failed" || item.result);
  const run = route.id ? await api.run(route.id) : runs[0];
  if (!run) return el("div", {class: "page"},
    pageHeading(config.modules[3], null), empty("成功执行一个批次后可以查看、签核和下载证据。"),
    el("a", {href: "#runs"}, config.modules[2]));

  const review = run.status === "done" ? await api.review(run.id) : null;
  const issue = errorText();
  const button = el("button", {type: "button", onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await download(await api.export(run.id), `evidence-${run.id}.json`);
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, "导出证据");
  button.disabled = run.status !== "done";
  const signing = el("div", {});
  if (run.status === "done") {
    signing.append(review ? signature(review) : user.role === "reviewer" ?
      reviewForm(run.id, data => signing.replaceChildren(signature(data))) : empty("待复核员签署。"));
  }
  const options = el("select", {"aria-label": "选择批次", onchange: event => {
    location.hash = `evidence/${event.target.value}`;
  }}, runs.map(item => el("option", {value: item.id, selected: item.id === run.id}, `#${item.id} ${item.case_title}`)));
  const result = run.result;
  return el("div", {class: "page evidence"},
    pageHeading(config.modules[3], run.case_title, button),
    issue, options,
    result ? el("section", {class: "feature evidence-summary"}, el("div", {}, el("h2", {}, "批次判定"), el("p", {}, result.summary)),
      el("details", {}, el("summary", {}, "证据摘要"), el("code", {}, run.digest))) :
      empty("批次仍在回放，先接收纸质收据挂账；回放完成后再判缺件并复核签署。"),
    result ? el("dl", {class: "metrics"}, Object.entries(result.metrics).map(([label, value]) =>
      el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))) : null,
    result ? section("业务结果明细", result.rows.length ? resultTable(result.rows) : empty("此批次无明细。")) : null,
    result ? section("复核签署", signing) : null,
    section("纸质收据对账", receiptsPanel(run.id, user)));
}
