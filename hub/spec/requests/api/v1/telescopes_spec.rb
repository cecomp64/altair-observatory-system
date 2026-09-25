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

    it "adds the api_revision 1 fields: NINA name, project, optical train, timezone, schedule_count" do
      project = create(:project, name: "Andromeda deep", priority: 7, completion_basis: "integrated")
      train = create(:optical_train, telescope: telescope, key: "esprit100_2600mm")
      target = create(:target, telescope: telescope, project: project, user: project.user, name: "M31",
                               optical_train: train, rotation_deg: 35, status: :active)
      create(:exposure_plan, target: target, filter: "Ha", desired_count: 20, completed_count: 12, usable_count: 9)

      get "/api/v1/telescopes/#{telescope.slug}/active_targets",
        headers: { "Authorization" => "Bearer #{api_key.plaintext_token}" }

      json = response.parsed_body
      expect(json["telescope"]["timezone"]).to eq(telescope.timezone)
      entry = json["targets"].sole
      expect(entry).to include(
        "nina_name" => "##{target.id} M31", "rotation_deg" => 35.0, "min_altitude_deg" => 30.0,
        "optical_train" => { "key" => "esprit100_2600mm" },
        "project" => { "id" => project.id, "name" => "Andromeda deep", "priority" => 7, "ts_project_name" => "#P#{project.id} Andromeda deep" }
      )
      # Integrated basis: the 3 accepted-but-unusable frames are scheduled again.
      expect(entry["exposure_plans"].sole["schedule_count"]).to eq(23)
    end
  end
end
