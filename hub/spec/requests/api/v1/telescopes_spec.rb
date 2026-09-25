require "rails_helper"

RSpec.describe "Api::V1::Telescopes", type: :request do
  let(:telescope) { create(:telescope, slug: "test-scope") }
  let(:other_telescope) { create(:telescope, slug: "other-scope") }
  let(:api_key) { create(:api_key, telescope: telescope) }

  describe "GET /api/v1/telescopes/:id/active_targets" do
    it "requires a valid API key" do
      get "/api/v1/telescopes/#{telescope.slug}/active_targets"
      expect(response).to have_http_status(:unauthorized)
    end

    it "rejects a key scoped to a different telescope" do
      other_key = create(:api_key, telescope: other_telescope)
      get "/api/v1/telescopes/#{telescope.slug}/active_targets",
        headers: { "Authorization" => "Bearer #{other_key.plaintext_token}" }

      expect(response).to have_http_status(:forbidden)
    end

    it "returns schedulable targets with their exposure plans, excluding drafts" do
      schedulable = create(:target, telescope: telescope, status: :submitted)
      create(:exposure_plan, target: schedulable, filter: "Luminance", desired_count: 20, completed_count: 5)
      create(:target, telescope: telescope, status: :draft)

      get "/api/v1/telescopes/#{telescope.slug}/active_targets",
        headers: { "Authorization" => "Bearer #{api_key.plaintext_token}" }

      expect(response).to have_http_status(:ok)
      json = response.parsed_body
      expect(json["targets"].map { |t| t["id"] }).to eq([ schedulable.id ])
      expect(json["targets"].first["exposure_plans"].first).to include(
        "filter" => "Luminance", "desired_count" => 20, "completed_count" => 5, "remaining_count" => 15
      )
    end
  end
end
