require "rails_helper"
require_relative "../../support/api_contract"

# The Hub's API responses, and the requests the worker sends, must match the
# shared contract in ../contracts (docs/SYSTEM_ARCHITECTURE.md §5, §11).
RSpec.describe "API contract", type: :request do
  let(:telescope) { create(:telescope) }
  let(:api_key) { create(:api_key, telescope: telescope) }
  let(:target) { create(:target, telescope: telescope, status: :in_progress) }
  let!(:plan) { create(:exposure_plan, target: target, desired_count: 20, completed_count: 5) }
  let(:headers) { { "Authorization" => "Bearer #{api_key.plaintext_token}", "Content-Type" => "application/json" } }

  it "serves active_targets in the contract shape" do
    get "/api/v1/telescopes/#{telescope.slug}/active_targets", headers: headers

    expect(response).to have_http_status(:ok)
    expect(response).to match_api_contract("worker/active_targets.response.json")
  end

  it "accepts a contract progress request and answers in the contract shape" do
    body = { exposure_plans: [ { id: plan.id, completed_count: 12 } ] }
    expect(body.deep_stringify_keys).to match_api_contract("worker/progress.request.json")

    patch "/api/v1/targets/#{target.id}/progress", params: body.to_json, headers: headers

    expect(response).to have_http_status(:ok)
    expect(response).to match_api_contract("worker/progress.response.json")
  end

  it "accepts a contract event request and answers in the contract shape" do
    body = { event_type: "error", payload: { message: "Target Scheduler DB locked" } }
    expect(body.deep_stringify_keys).to match_api_contract("worker/target_event.request.json")

    post "/api/v1/targets/#{target.id}/events", params: body.to_json, headers: headers

    expect(response).to have_http_status(:created)
    expect(response).to match_api_contract("worker/target_event.response.json")
  end

  it "accepts a contract (legacy) file request and answers in the contract shape" do
    body = { url: "https://bucket.s3.amazonaws.com/stack_Ha.fits", kind: "stacked", filter: "Ha" }
    expect(body.deep_stringify_keys).to match_api_contract("worker/target_file.request.json")

    post "/api/v1/targets/#{target.id}/files", params: body.to_json, headers: headers

    expect(response).to have_http_status(:created)
    expect(response).to match_api_contract("worker/target_file.response.json")
  end

  it "answers auth failures with the contract error shape" do
    get "/api/v1/telescopes/#{telescope.slug}/active_targets", headers: { "Authorization" => "Bearer nope" }

    expect(response).to have_http_status(:unauthorized)
    expect(response).to match_api_contract("shared/error.response.json")
  end

  it "rejects a payload that breaks the contract" do
    expect(ApiContract.errors("worker/progress.request.json", { "exposure_plans" => [ { "id" => 1 } ] })).not_to be_empty
  end
end
