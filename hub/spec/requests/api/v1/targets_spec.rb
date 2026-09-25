require "rails_helper"

RSpec.describe "Api::V1::Targets", type: :request do
  let(:telescope) { create(:telescope) }
  let(:api_key) { create(:api_key, telescope: telescope) }
  let(:target) { create(:target, telescope: telescope, status: :in_progress) }
  let(:auth_headers) { { "Authorization" => "Bearer #{api_key.plaintext_token}" } }

  describe "PATCH /api/v1/targets/:id/progress" do
    it "updates exposure plan counts and records a progress event" do
      plan = create(:exposure_plan, target: target, desired_count: 20, completed_count: 5)

      patch "/api/v1/targets/#{target.id}/progress",
        params: { exposure_plans: [ { id: plan.id, completed_count: 12 } ] }.to_json,
        headers: auth_headers.merge("Content-Type" => "application/json")

      expect(response).to have_http_status(:ok)
      expect(plan.reload.completed_count).to eq(12)
      expect(target.target_events.progress.count).to eq(1)
    end

    it "marks the target completed once every exposure plan hits its desired count" do
      plan = create(:exposure_plan, target: target, desired_count: 5, completed_count: 4)

      patch "/api/v1/targets/#{target.id}/progress",
        params: { exposure_plans: [ { id: plan.id, completed_count: 5 } ] }.to_json,
        headers: auth_headers.merge("Content-Type" => "application/json")

      expect(target.reload).to be_completed
    end

    it "rejects a key scoped to a different telescope" do
      other_key = create(:api_key)

      patch "/api/v1/targets/#{target.id}/progress",
        params: { exposure_plans: [] }.to_json,
        headers: { "Authorization" => "Bearer #{other_key.plaintext_token}", "Content-Type" => "application/json" }

      expect(response).to have_http_status(:forbidden)
    end
  end

  describe "POST /api/v1/targets/:id/files" do
    it "registers a file and emits a file_added event" do
      post "/api/v1/targets/#{target.id}/files",
        params: { url: "https://bucket.s3.amazonaws.com/sub.fits", kind: "sub", filter: "Ha" }.to_json,
        headers: auth_headers.merge("Content-Type" => "application/json")

      expect(response).to have_http_status(:created)
      expect(target.target_files.count).to eq(1)
      expect(target.target_events.file_added.count).to eq(1)
    end

    it "updates the target's preview image when kind is preview" do
      post "/api/v1/targets/#{target.id}/files",
        params: { url: "https://bucket.s3.amazonaws.com/preview.jpg", kind: "preview" }.to_json,
        headers: auth_headers.merge("Content-Type" => "application/json")

      expect(target.reload.preview_image_url).to eq("https://bucket.s3.amazonaws.com/preview.jpg")
    end
  end
end
