require "rails_helper"

# P3 exit criterion: a replayed synthetic night produces correct counters,
# and replaying any batch N times gives the same state (§11 idempotency).
RSpec.describe "Replayed synthetic night", type: :request do
  let(:telescope) { create(:telescope, slug: "backyard-16in") }
  let!(:train) { create(:optical_train, telescope: telescope, key: "esprit100_2600mm") }
  let(:project) { create(:project, completion_basis: "integrated") }
  let!(:target) { create(:target, project: project, user: project.user, telescope: telescope, optical_train: train, name: "M31", status: :in_progress) }
  let!(:ha) { create(:exposure_plan, target: target, filter: "Ha", exposure_seconds: 300, desired_count: 4, completed_count: 6) }
  let!(:oiii) { create(:exposure_plan, target: target, filter: "OIII", exposure_seconds: 300, desired_count: 2, completed_count: 2) }
  let(:node) { create(:processing_node).tap { |n| n.telescopes << telescope } }
  let(:node_key) { create(:node_api_key, processing_node: node) }

  def replay_night
    lights = (1..6).map { |i| frame_payload(i) } +
             [ frame_payload(7, "filter" => "OIII"), frame_payload(8, "filter" => "O3"),
               frame_payload(9, "exposure_s" => 60), # matches no plan
               frame_payload(10, "status" => "invalid") ]
    calibration = [ frame_payload(11, "image_type" => "flat", "target_id" => nil, "exposure_s" => 2) ]
    post "/api/v1/processing/frames:batch", params: { frames: lights + calibration }.to_json, headers: node_headers
    patch "/api/v1/processing/frames:batch", params: { frames: [ { sha256: sha(6), status: "rejected", status_reason: "FWHM" } ] }.to_json, headers: node_headers
    put "/api/v1/processing/data_products/night_master/1",
      params: { metadata: { target_id: target.id, night: "2026-09-24", filter: "Ha", sha256: sha(90), size_bytes: 1, archive_uri: "s3://b/n.xisf",
                            metrics: { frames: 4, rejected: 1, frame_sha256s: (1..4).map { |i| sha(i) } } }.to_json },
      headers: { "Authorization" => "Bearer #{node_key.plaintext_token}" }
    put "/api/v1/processing/data_products/multi_night_master/2",
      params: { metadata: { target_id: target.id, version: 1, filter: "Ha", sha256: sha(91), size_bytes: 1, archive_uri: "s3://b/m.xisf",
                            metrics: { frames: 3, frame_sha256s: (1..3).map { |i| sha(i) } } }.to_json },
      headers: { "Authorization" => "Bearer #{node_key.plaintext_token}" }
    ProgressRecomputeJob.perform_now([ target.id ])
  end

  def counters
    target.exposure_plans.order(:id).map { |p| p.reload.slice(:collected_count, :usable_count, :integrated_count, :integrated_seconds) }
  end

  it "ends with correct counters, and the same counters after replaying it" do
    replay_night
    expect(counters).to eq([
      { "collected_count" => 6, "usable_count" => 4, "integrated_count" => 3, "integrated_seconds" => 900 },
      { "collected_count" => 2, "usable_count" => 0, "integrated_count" => 0, "integrated_seconds" => 0 }
    ])
    expect(Frame.find_by!(sha256: sha(9)).exposure_plan).to be_nil
    expect(ha.reload.schedule_count).to eq(6) # 4 desired + (6 accepted - 4 usable)

    first = counters
    2.times { replay_night }
    expect(counters).to eq(first)
    expect(Frame.count).to eq(11)
  end

  it "completes the target on the integrated basis only once masters cover every plan" do
    replay_night
    expect(target.reload).to be_in_progress

    put "/api/v1/processing/data_products/multi_night_master/3",
      params: { metadata: { target_id: target.id, version: 2, filter: "Ha", sha256: sha(92), size_bytes: 1, archive_uri: "s3://b/m2.xisf",
                            metrics: { frames: 4, frame_sha256s: (1..4).map { |i| sha(i) } }, supersedes_altair_id: 2 }.to_json },
      headers: { "Authorization" => "Bearer #{node_key.plaintext_token}" }
    put "/api/v1/processing/data_products/multi_night_master/4",
      params: { metadata: { target_id: target.id, version: 1, filter: "OIII", sha256: sha(93), size_bytes: 1, archive_uri: "s3://b/o.xisf",
                            metrics: { frames: 2, frame_sha256s: [ sha(7), sha(8) ] } }.to_json },
      headers: { "Authorization" => "Bearer #{node_key.plaintext_token}" }
    ProgressRecomputeJob.perform_now([ target.id ])

    expect(target.reload).to be_completed
  end
end
