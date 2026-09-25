require "rails_helper"

RSpec.describe "API key scopes and principals", type: :request do
  let(:telescope) { create(:telescope) }
  let(:target) { create(:target, telescope: telescope) }

  def auth(key) = { "Authorization" => "Bearer #{key.plaintext_token}", "Content-Type" => "application/json" }

  it "forbids an action whose scope the key lacks" do
    key = create(:api_key, telescope: telescope, scopes: %w[targets:read])
    patch "/api/v1/targets/#{target.id}/progress", params: { exposure_plans: [] }.to_json, headers: auth(key)

    expect(response).to have_http_status(:forbidden)
    expect(response.parsed_body["error"]).to include("progress:write")
  end

  it "gives new telescope keys the worker scopes and node keys the processing scopes" do
    expect(create(:api_key).scopes).to include("targets:read", "progress:write", "sessions:write")
    expect(create(:node_api_key).scopes).to include("frames:write", "commands:read")
    expect(create(:node_api_key).scopes).not_to include("progress:write")
  end

  it "lets a processing node read active targets only for telescopes it serves" do
    node = create(:processing_node)
    node.telescopes << telescope
    key = create(:node_api_key, processing_node: node)
    other = create(:telescope)

    get "/api/v1/telescopes/#{telescope.slug}/active_targets", headers: auth(key)
    expect(response).to have_http_status(:ok)

    get "/api/v1/telescopes/#{other.slug}/active_targets", headers: auth(key)
    expect(response).to have_http_status(:forbidden)
  end

  it "rejects unknown scopes" do
    expect(build(:api_key, scopes: %w[admin:everything])).not_to be_valid
  end
end
