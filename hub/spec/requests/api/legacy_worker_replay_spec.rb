require "rails_helper"

# Replays the requests the worker sent before the unified-platform work
# (spec/fixtures/worker_requests/pre_p1.json) against the current API. Every
# phase must keep them working, byte for byte on the worker's side.
RSpec.describe "Legacy worker replay", type: :request do
  fixture = JSON.parse(Rails.root.join("spec/fixtures/worker_requests/pre_p1.json").read)

  let(:telescope) { create(:telescope, slug: "backyard-16in") }
  # A key created the way P1's migration converts pre-existing keys.
  let(:api_key) { create(:api_key, telescope: telescope, scopes: %w[targets:read progress:write events:write sessions:write files:write heartbeat:write]) }
  let(:target) { create(:target, telescope: telescope, status: :in_progress) }
  let!(:plan) { create(:exposure_plan, target: target, filter: "Ha", desired_count: 20, completed_count: 5) }

  def substitute(value)
    json = value.to_json
      .gsub("{slug}", telescope.slug).gsub("{target_id}", target.id.to_s)
      .gsub("\"{plan_id}\"", plan.id.to_s).gsub("{token}", api_key.plaintext_token)
    JSON.parse(json)
  end

  def dig_all(data, path)
    path.split(".").reduce([ data ]) do |nodes, key|
      key == "*" ? nodes.flat_map { |n| Array(n) } : nodes.map { |n| n.is_a?(Hash) ? n.fetch(key) { raise KeyError, "missing #{path}" } : n }
    end
  end

  fixture["requests"].each do |recorded|
    it "still answers: #{recorded['name']}" do
      headers = substitute(fixture["headers"])
      path = substitute(recorded["path"])
      body = recorded["body"] && substitute(recorded["body"]).to_json

      process recorded["method"].downcase.to_sym, path, params: body, headers: headers

      expect(response.status).to eq(recorded["status"]), response.body
      data = JSON.parse(response.body)
      recorded["reads"].each { |field| expect { dig_all(data, field) }.not_to raise_error }
      expect(data["targets"]).not_to be_empty if path.end_with?("active_targets")
    end
  end
end
