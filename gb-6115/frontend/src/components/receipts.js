import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, section } from "../components/common/panel.js";
import { table } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, time } from "../utils/format.js";

const REPLAY = Object.freeze({
  replaying: "批次仍在回放：收到的收据先挂账，回放完成后再判缺件。",
  done: "批次已回放完成：可按炉号与证书号对账。",
  failed: "批次回放失败，无法补录或对账。",
});

const VERDICT_KEYS = ["判定", "登记炉号", "登记证书号", "收据炉号", "收据证书号", "登记号", "收据时间 s", "说明"];

function backfillForm(runId, done) {
  const form = el("form", {id: "receipt-form"},
    field("登记号", el("input", {name: "registration_no", required: true, maxlength: 32, placeholder: "如 PAPER-0001"})),
    field("炉号", el("input", {name: "asset", required: true, maxlength: 24, placeholder: "如 OV-203"})),
    field("证书号", el("input", {name: "certificate", required: true, maxlength: 24, placeholder: "如 RET-1124-01"})),
    field("收据时间 s", el("input", {name: "archived_at", type: "number", min: 0, step: 1})),
    el("small", {}, "登记号是补录的幂等键：同一登记号重试不重复入账，内容有变则退回。"),
    errorText(), el("button", {type: "submit"}, "补录入账"));
  submit(form, async values => {
    const archived = values.get("archived_at");
    const payload = {
      registration_no: values.get("registration_no").trim(),
      asset: values.get("asset").trim(),
      certificate: values.get("certificate").trim(),
      archived_at: archived === "" ? null : Number(archived),
    };
    const entry = await api.addReceipt(runId, payload);
    done(entry);
  });
  return form;
}

function roundList(overview, runId, canSeal, refresh) {
  return el("ul", {class: "round-list"},
    overview.rounds.map(round => {
      const latest = round.id === overview.current.round_id;
      const sealable = canSeal && latest && round.status === "open";
      const button = el("button", {type: "button", disabled: !sealable || undefined, title: sealable ? "" : round.status === "sealed" ? "已封存" : "只能封存最新盘点"}, "封存盘点");
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await api.sealRound(runId, round.id);
          await refresh();
        } catch (error) {
          button.insertAdjacentText("afterend", error.message);
          button.disabled = false;
        }
      });
      return el("li", {class: "round"},
        el("div", {class: "round-head"},
          el("strong", {}, `第 ${round.seq} 次盘点 · ${round.status === "sealed" ? "已封存" : "进行中"}`),
          latest && sealable ? button : null),
        el("p", {}, `登记 ${round.created_by} · ${time(round.created_at)}${round.sealed_at ? ` · 封存 ${round.sealed_by} · ${time(round.sealed_at)}` : ""}`),
        round.conclusion_digest ? el("details", {}, el("summary", {}, "封存结论摘要"), el("code", {}, round.conclusion_digest)) : null,
        round.entries.length ? table(["登记号", "炉号", "证书号", "收据时间 s"],
          round.entries.map(entry => [entry.registration_no, entry.asset, entry.certificate, entry.archived_at]), `第 ${round.seq} 次盘点补录`) :
          empty("本次盘点还没有补录收据。"));
    }));
}

export function receiptsPanel(runId, user) {
  const root = el("div", {class: "stack"});
  let overview;

  async function render() {
    overview = await api.receipts(runId);
    const current = overview.current;
    const operator = user.role === "operator";
    const sealable = operator && runId && overview.replay === "done";
    const verdicts = current.verdicts;
    const note = current.sealed
      ? "本次盘点已封存；封存后收到的补录会另开盘点，原结论连同凭据留查。"
      : REPLAY[overview.replay];
    root.replaceChildren(
      el("p", {class: "receipt-note", "data-replay": overview.replay}, note),
      current.sealed && current.conclusion_digest ?
        el("details", {}, el("summary", {}, "封存结论摘要"), el("code", {}, current.conclusion_digest)) : null,
      el("dl", {class: "metrics"}, Object.entries(current.metrics).map(([label, value]) =>
        el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))),
      verdicts.length ? table(VERDICT_KEYS, verdicts.map(verdict => VERDICT_KEYS.map(key => verdict[key])), "对账明细") :
        empty(overview.replay === "replaying" ? "暂无挂账收据。" : "此批次没有可对账的登记或收据。"),
      section("补录纸质收据", operator && overview.replay !== "failed" ? backfillForm(runId, render) : empty("当前账号可查看对账结果。")),
      section("盘点与封存", overview.rounds.length ? roundList(overview, runId, sealable, render) : empty("补录第一条收据即开启盘点。")));
  }

  root.append(el("p", {class: "empty"}, "正在载入对账…"));
  render().catch(error => root.replaceChildren(el("p", {role: "alert"}, error.message)));
  return root;
}
