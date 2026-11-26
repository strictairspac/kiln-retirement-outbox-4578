import { request } from "./http.js";

export const api = {
  config: () => request("/workbench"),
  login: data => request("/auth/login", {method: "POST", data}),
  cases: () => request("/cases"),
  createCase: data => request("/cases", {method: "POST", data}),
  runs: () => request("/runs"),
  run: (id, signal) => request(`/runs/${id}`, {signal}),
  enqueue: (id, data) => request(`/cases/${id}/runs`, {method: "POST", data}),
  review: id => request(`/runs/${id}/review`),
  submitReview: (id, data) => request(`/runs/${id}/review`, {method: "POST", data}),
  receipts: (id, signal) => request(`/runs/${id}/receipts`, {signal}),
  addReceipt: (id, data) => request(`/runs/${id}/receipts`, {method: "POST", data}),
  sealRound: (id, roundId) => request(`/runs/${id}/receipts/rounds/${roundId}/seal`, {method: "POST"}),
  export: id => request(`/runs/${id}/export`, {raw: true}),
  audit: () => request("/audit", {signal: AbortSignal.timeout(5000)}),
};
