require "rails_helper"

RSpec.describe "Processing API", type: :request do
  let(:telescope) { create(:telescope, slug: "backyard-16in") }
  let!(:train) { create(:optical_train, telescope: telescope, key: "esprit100_2600mm") }
  let(:project) { create(:project) }
  let!(:target) { create(:target, project: project, user: project.user, telescope: telescope, optical_train: train, name: "M31", ra_deg: 10.68479, dec_deg: 41.26906) }
  let!(:plan) { create(:exposure_plan, target: target, filter: "Ha", exposure_seconds: 300, desired_count: 10) }
  let(:node) { create(:processing_node, name: "altair-proc-01").tap { |n| n.telescopes << telescope } }
  let(:node_key) { create(:node_api_key, processing_node: node) }

  def post_frames(frames, headers: node_headers)
    post "/api/v1/processing/frames:batch", params: { frames: frames }.to_json, headers: headers
  end

  describe "authentication and scopes" do
    it "rejects worker keys and keys without the scope" do
      worker = create(:api_key, telescope: telescope)
      get "/api/v1/processing/config", headers: node_headers(worker)
      expect(response).to have_http_status(:forbidden)

      narrow = create(:node_api_key, processing_node: node, scopes: %w[targets:read])
      post_frames([ frame_payload(1) ], headers: node_headers(narrow))
      expect(response).to have_http_status(:forbidden)
      expect(response).to match_api_contract("shared/error.response.json")
    end
  end

  describe "GET /config" do
    it "returns the node's world in the contract shape, with an ETag" do
      target.astro_object = create(:astro_object, primary_name: "Andromeda Galaxy").tap { |o| o.add_alias("M 31") }
      target.save!
      create(:target, telescope: telescope, status: :draft) # drafts stay out
      create(:target, status: :active) # another telescope: stays out

      get "/api/v1/processing/config", headers: node_headers

      expect(response).to have_http_status(:ok)
      expect(response).to match_api_contract("processing/config.response.json")
      body = response.parsed_body
      expect(body["targets"].map { |t| t["id"] }).to eq([ target.id ])
      expect(body["targets"].first).to include("nina_name" => "##{target.id} M31", "optical_train" => train.key)
      expect(body["targets"].first["aliases"]).to include("M31", "Andromeda Galaxy", "M 31")
      expect(body["telescopes"].first["optical_trains"].first["filters"].first).to include("name" => "Ha", "aliases" => [ "H-alpha", "HA" ])

      get "/api/v1/processing/config", headers: node_headers.merge("If-None-Match" => response.headers["ETag"])
      expect(response).to have_http_status(:not_modified)

      target.update!(name: "M31 deep")
      get "/api/v1/processing/config", headers: node_headers.merge("If-None-Match" => response.headers["ETag"].to_s)
      expect(response).to have_http_status(:ok)
    end
  end

  describe "POST /frames:batch" do
    it "upserts frames, links plans, and answers per item in the contract shape" do
      frames = [ frame_payload(1), frame_payload(2, "target_id" => nil, "assignment_source" => "unlinked", "object_header" => "Andromeda?"),
                 frame_payload(3, "optical_train" => "nope") ]
      expect(frames.first(2)).to all(satisfy { |f| ApiContract.errors("processing/frames_batch.request.json", { "frames" => [ f ] }).empty? })

      post_frames(frames)

      expect(response).to have_http_status(:ok)
      expect(response).to match_api_contract("processing/frames_batch.response.json")
      ok, unlinked, error = response.parsed_body["results"]
      expect(ok).to include("status" => "ok", "target_id" => target.id, "exposure_plan_id" => plan.id)
      expect(unlinked).to include("status" => "ok", "target_id" => nil)
      expect(error).to include("status" => "error")
      expect(Frame.find_by!(sha256: sha(1))).to have_attributes(project_id: project.id, assignment_source: "header_token", filter: "Ha")
    end

    it "is idempotent" do
      2.times { post_frames([ frame_payload(1), frame_payload(2) ]) }
      expect(Frame.count).to eq(2)
      perform_enqueued_jobs(only: ProgressRecomputeJob) { ProgressRecomputeJob.perform_now([ target.id ]) }
      expect(plan.reload.collected_count).to eq(2)
    end

    it "never lets Altair override a manual assignment, and returns the manual target" do
      other = create(:target, project: project, user: project.user, telescope: telescope, name: "M32")
      post_frames([ frame_payload(1) ])
      Frames::Assigner.assign!(Frame.where(sha256: sha(1)), target: other, user: project.user)

      post_frames([ frame_payload(1) ])

      expect(response.parsed_body["results"].first["target_id"]).to eq(other.id)
      expect(Frame.find_by!(sha256: sha(1))).to have_attributes(target_id: other.id, assignment_source: "manual")
      expect(node.processing_commands.find_by!(kind: "assign_frames").payload).to eq("target_id" => other.id, "sha256s" => [ sha(1) ])
    end

    it "stores a target on another telescope as unlinked, and flags unknown filters" do
      foreign = create(:target, status: :active)
      post_frames([ frame_payload(1, "target_id" => foreign.id, "filter" => "Weird") ])
      frame = Frame.find_by!(sha256: sha(1))
      expect(frame).to have_attributes(target_id: nil, assignment_source: "unlinked", filter: "Weird", filter_known: false)
    end

    it "maps filter aliases to the canonical name" do
      post_frames([ frame_payload(1, "filter" => "h-alpha") ])
      expect(Frame.find_by!(sha256: sha(1)).filter).to eq("Ha")
    end

    it "emits one frames_collected event per target, night and filter per hour" do
      post_frames([ frame_payload(1), frame_payload(2) ])
      post_frames([ frame_payload(3) ])
      events = target.target_events.frames_collected
      expect(events.count).to eq(1)
      expect(events.first.payload).to include("night" => "2026-09-24", "filter" => "Ha", "count" => 3)
    end

    it "caps batches at 500" do
      post_frames(Array.new(501) { |i| frame_payload(i) })
      expect(response).to have_http_status(:unprocessable_content)
    end

    it "queues FOV matching for new pointings" do
      expect { post_frames([ frame_payload(1) ]) }.to have_enqueued_job(FrameFovMatchJob)
    end
  end

  describe "PATCH /frames:batch" do
    it "updates status, quality and storage, merging objects" do
      post_frames([ frame_payload(1) ])
      body = { frames: [ { sha256: sha(1), quality: { fwhm: 2.4 }, storage: { s3: "DEEP_ARCHIVE" } }, { sha256: sha(9), status: "rejected" } ] }
      expect(body.deep_stringify_keys).to match_api_contract("processing/frames_patch.request.json")

      patch "/api/v1/processing/frames:batch", params: body.to_json, headers: node_headers

      expect(response).to match_api_contract("processing/frames_batch.response.json")
      frame = Frame.find_by!(sha256: sha(1))
      expect(frame.quality).to eq("fwhm" => 2.4)
      expect(frame.storage).to eq("nas" => true, "s3" => "DEEP_ARCHIVE")
      expect(response.parsed_body["results"].last).to include("status" => "error")
    end
  end

  describe "nights" do
    it "upserts the night, announces it closed once, and serves a digest that matches the frames" do
      post_frames([ frame_payload(1), frame_payload(2), frame_payload(3, "image_type" => "flat", "target_id" => nil) ])
      body = { state: "closed", closed_by: "session_end", lights_count: 2, calibration_count: 1, light_seconds: 600 }
      expect(body.deep_stringify_keys).to match_api_contract("processing/night.request.json")

      2.times { put "/api/v1/processing/nights/#{train.key}/2026-09-24", params: body.to_json, headers: node_headers }

      expect(ObservingNight.find_by!(optical_train: train, night: "2026-09-24")).to have_attributes(state: "closed", lights_count: 2)
      expect(target.target_events.night_closed.count).to eq(1)

      get "/api/v1/processing/nights/#{train.key}/2026-09-24/digest", headers: node_headers
      expect(response).to match_api_contract("processing/night_digest.response.json")
      xor = [ sha(1), sha(2), sha(3) ].map { |h| [ h ].pack("H*").bytes }.transpose.map { |bytes| bytes.reduce(:^) }.pack("C*").unpack1("H*")
      expect(response.parsed_body).to eq("frame_count" => 3, "sha256_xor" => xor, "by_type" => { "light" => 2, "flat" => 1 })
    end

    it "refuses optical trains the node doesn't serve" do
      put "/api/v1/processing/nights/#{create(:optical_train).key}/2026-09-24", params: { state: "open" }.to_json, headers: node_headers
      expect(response).to have_http_status(:not_found)
    end
  end

  describe "PUT /calibration_masters/:altair_id and /jobs/:altair_id" do
    it "upserts by altair id" do
      master = { optical_train: train.key, kind: "flat", filter: "Ha", n_frames: 40, sha256: sha(7) }
      expect(master.deep_stringify_keys).to match_api_contract("processing/calibration_master.request.json")
      2.times { put "/api/v1/processing/calibration_masters/55", params: master.to_json, headers: node_headers }
      expect(node.calibration_masters.count).to eq(1)

      job = { kind: "NIGHT_STACK", status: "running", target_id: target.id, night: "2026-09-24", filter: "Ha" }
      expect(job.deep_stringify_keys).to match_api_contract("processing/job.request.json")
      put "/api/v1/processing/jobs/9", params: job.to_json, headers: node_headers
      put "/api/v1/processing/jobs/9", params: job.merge(status: "succeeded").to_json, headers: node_headers
      expect(node.processing_jobs.sole).to have_attributes(status: "succeeded", target: target)
    end
  end

  describe "PUT /data_products/:kind/:altair_id" do
    let(:jpeg) { Rack::Test::UploadedFile.new(StringIO.new("\xFF\xD8\xFF\xE0preview".b), "image/jpeg", original_filename: "p.jpg") }

    def put_product(altair_id, meta, files = {})
      expect(meta.deep_stringify_keys).to match_api_contract("processing/data_product.metadata.json")
      put "/api/v1/processing/data_products/multi_night_master/#{altair_id}",
        params: { metadata: meta.to_json, **files }, headers: { "Authorization" => "Bearer #{node_key.plaintext_token}" }
    end

    it "stores a master with previews, supersedes the previous version, and counts integrated frames" do
      post_frames([ frame_payload(1), frame_payload(2), frame_payload(3) ])
      put_product(1, { target_id: target.id, version: 1, filter: "Ha", sha256: sha(50), size_bytes: 10, archive_uri: "s3://b/v1.xisf",
                       metrics: { frames: 1, frame_sha256s: [ sha(1) ] } })
      put_product(2, { target_id: target.id, version: 2, filter: "Ha", sha256: sha(51), size_bytes: 10, archive_uri: "s3://b/v2.xisf",
                       metrics: { frames: 2, frame_sha256s: [ sha(1), sha(2) ] }, supersedes_altair_id: 1 }, preview: jpeg, thumbnail: jpeg)

      expect(response).to have_http_status(:ok)
      v1, v2 = DataProduct.multi_night_master.order(:altair_id)
      expect(v1.superseded_by).to eq(v2)
      expect(v2.preview).to be_attached
      expect(target.target_events.master_updated.count).to eq(2)

      ProgressRecomputeJob.perform_now([ target.id ])
      expect(plan.reload).to have_attributes(collected_count: 3, integrated_count: 2, integrated_seconds: 600)
    end

    it "rejects a non-JPEG preview and an unknown kind" do
      svg = Rack::Test::UploadedFile.new(StringIO.new("<svg/>"), "image/svg+xml", original_filename: "p.svg")
      put_product(3, { target_id: target.id, filter: "Ha", sha256: sha(52), size_bytes: 1, archive_uri: nil }, preview: svg)
      expect(response).to have_http_status(:unprocessable_content)

      put "/api/v1/processing/data_products/sub/4", params: { metadata: "{}" }, headers: { "Authorization" => "Bearer #{node_key.plaintext_token}" }
      expect(response).to have_http_status(:unprocessable_content)
    end
  end

  describe "PUT /issues/:fingerprint" do
    let(:fingerprint) { "FLAT_MISSING:#{train.key}:2026-09-24:Ha" }
    let(:body) { { kind: "FLAT_MISSING", severity: "blocking", status: "open", message: "Ha flats needed at rotator 31250", target_id: target.id, night: "2026-09-24", filter: "Ha", scope: { rotator_pos: 31250 } } }

    it "upserts by fingerprint and notifies owner and admins on open and resolve, not on repeats" do
      expect(body.deep_stringify_keys).to match_api_contract("processing/issue.request.json")
      create(:user, :admin)

      expect {
        2.times { put "/api/v1/processing/issues/#{ERB::Util.url_encode(fingerprint)}", params: body.to_json, headers: node_headers }
      }.to have_enqueued_job(AdminAlertJob).exactly(:once)
      issue = node.processing_issues.sole
      expect(issue).to have_attributes(fingerprint: fingerprint, project: project, optical_train: train, status: "open")
      expect(target.target_events.issue_opened.count).to eq(1)

      put "/api/v1/processing/issues/#{ERB::Util.url_encode(fingerprint)}", params: body.merge(status: "resolved").to_json, headers: node_headers
      expect(issue.reload).to have_attributes(status: "resolved")
      expect(issue.resolved_at).to be_present
      expect(target.target_events.issue_resolved.count).to eq(1)
    end

    it "sends infrastructure issues to admins only" do
      put "/api/v1/processing/issues/NAS_SPACE_LOW", params: { kind: "NAS_SPACE_LOW", severity: "warning", status: "open", message: "NAS 8% free" }.to_json, headers: node_headers
      expect(TargetEvent.issue_opened.count).to eq(0)
      expect(AdminAlertJob).to have_been_enqueued
    end
  end

  describe "commands" do
    it "delivers pending commands once, in the contract shape, and records acks" do
      ready = node.processing_commands.create!(kind: "night_ready", payload: { optical_train: train.key, night: "2026-09-24", at: "2026-09-25T12:41:00Z", closed_by: "session_end" })
      node.processing_commands.create!(kind: "refresh_config", payload: {})

      get "/api/v1/processing/commands?state=pending", headers: node_headers
      expect(response).to match_api_contract("processing/commands.response.json")
      expect(response.parsed_body.size).to eq(2)
      get "/api/v1/processing/commands?state=pending", headers: node_headers
      expect(response.parsed_body).to eq([])

      ack = { state: "succeeded", result: { closed: true } }
      expect(ack.deep_stringify_keys).to match_api_contract("processing/command_ack.request.json")
      post "/api/v1/processing/commands/#{ready.id}/ack", params: ack.to_json, headers: node_headers
      expect(ready.reload).to have_attributes(state: "succeeded", result: { "closed" => true })
    end

    it "doesn't let a node read another node's commands" do
      other = create(:processing_node).processing_commands.create!(kind: "refresh_config", payload: {})
      post "/api/v1/processing/commands/#{other.id}/ack", params: { state: "succeeded" }.to_json, headers: node_headers
      expect(response).to have_http_status(:not_found)
    end
  end
end

RSpec.describe "Worker session and heartbeat API", type: :request do
  let(:telescope) { create(:telescope, slug: "backyard-16in") }
  let(:worker_key) { create(:api_key, telescope: telescope) }
  let(:target) { create(:target, telescope: telescope) }
  let(:headers) { { "Authorization" => "Bearer #{worker_key.plaintext_token}", "Content-Type" => "application/json" } }

  it "records roof and session events, and queues night_ready per optical train for every serving node" do
    node = create(:processing_node).tap { |n| n.telescopes << telescope }
    create(:optical_train, telescope: telescope, key: "second_train")
    body = { event: "session_end", at: "2026-09-25T12:41:00Z", night: "2026-09-24", target_ids: [ target.id ] }
    expect(body.deep_stringify_keys).to match_api_contract("worker/session_event.request.json")

    post "/api/v1/telescopes/#{telescope.slug}/sessions", params: { event: "roof_open", at: "2026-09-25T03:00:00Z", night: "2026-09-24" }.to_json, headers: headers
    post "/api/v1/telescopes/#{telescope.slug}/sessions", params: body.to_json, headers: headers

    expect(response).to have_http_status(:created)
    commands = node.processing_commands.where(kind: "night_ready")
    expect(commands.map { |c| c.payload["optical_train"] }).to contain_exactly(telescope.slug, "second_train")
    expect(commands.first.payload).to include("night" => "2026-09-24", "closed_by" => "session_end")
    expect(ApiContract.errors("processing/command.json", commands.first.as_api_json.deep_stringify_keys)).to be_empty
    night = ObservingNight.find_by!(optical_train: telescope.default_optical_train, night: "2026-09-24")
    expect(night.roof_open_at).to be_present
    expect(night.session_end_at).to be_present
    expect(target.target_events.session.count).to eq(1)
  end

  it "rejects another telescope" do
    post "/api/v1/telescopes/#{create(:telescope).slug}/sessions", params: { event: "roof_open" }.to_json, headers: headers
    expect(response).to have_http_status(:forbidden)
  end

  it "records heartbeats from workers and nodes" do
    body = { agent: "robs", version: "0.2.0", status: { ok: true } }
    expect(body.deep_stringify_keys).to match_api_contract("shared/heartbeat.request.json")
    post "/api/v1/heartbeat", params: body.to_json, headers: headers
    expect(telescope.reload.worker_last_heartbeat_at).to be_present

    node = create(:processing_node)
    node_key = create(:node_api_key, processing_node: node)
    post "/api/v1/heartbeat", params: { agent: "altair", version: "0.8.0", status: { outbox_depth: 3 } }.to_json,
      headers: { "Authorization" => "Bearer #{node_key.plaintext_token}", "Content-Type" => "application/json" }
    expect(node.reload).to be_healthy
    expect(node.status.dig("status", "outbox_depth")).to eq("3").or eq(3)
  end
end
