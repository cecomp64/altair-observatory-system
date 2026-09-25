require "rails_helper"

RSpec.describe "Processing UI", type: :request do
  let(:admin) { create(:user, :admin) }
  let(:owner) { create(:user) }
  let(:telescope) { create(:telescope) }
  let!(:train) { create(:optical_train, telescope: telescope) }
  let(:project) { create(:project, user: owner) }
  let!(:target) { create(:target, project: project, user: owner, telescope: telescope, optical_train: train, ra_deg: 10.68, dec_deg: 41.27) }
  let(:node) { create(:processing_node).tap { |n| n.telescopes << telescope } }
  let!(:frame) do
    Frame.create!(sha256: Digest::SHA256.hexdigest("a"), processing_node: node, telescope: telescope, optical_train: train, target: target,
                  project: project, image_type: "light", night: "2026-09-24", date_obs: Time.utc(2026, 9, 25, 6), filter: "Ha",
                  exposure_s: 300, file_name: "a.fits", logical_path: "raw/a.fits", ra_deg: 10.68, dec_deg: 41.27,
                  quality: { "fwhm" => 2.1 }, storage: { "nas" => true })
  end
  let!(:unassigned) do
    Frame.create!(sha256: Digest::SHA256.hexdigest("b"), processing_node: node, telescope: telescope, optical_train: train,
                  image_type: "light", night: "2026-09-24", date_obs: Time.utc(2026, 9, 25, 7), filter: "Ha", exposure_s: 300,
                  object_header: "Andromeda?", file_name: "b.fits", logical_path: "raw/b.fits", ra_deg: 10.7, dec_deg: 41.3)
  end
  let!(:issue) do
    ProcessingIssue.create!(processing_node: node, fingerprint: "FLAT_MISSING:x", kind: "FLAT_MISSING", severity: "blocking",
                            message: "Ha flats needed", target: target, project: project, optical_train: train, telescope: telescope,
                            night: "2026-09-24", filter: "Ha", opened_at: Time.current)
  end

  it "searches files by name, cone, filter and group, and exports CSV" do
    sign_in owner
    get frames_path(q: target.name)
    expect(response.body).to include("a.fits")
    expect(response.body).not_to include("b.fits") # not the owner's

    get frames_path(ra: "10.68", dec: "41.27", radius: "0.5", group: "night")
    expect(response.body).to include("2026-09-24")

    get frames_path(format: :csv, filter: "Ha")
    expect(response.body.lines.first).to start_with("sha256,file_name")
    expect(response.body).to include(frame.sha256)
  end

  it "shows a frame to its owner and hides it from others" do
    sign_in owner
    get frame_path(frame)
    expect(response.body).to include(frame.sha256, "NAS")
    sign_in create(:user)
    get frame_path(frame)
    expect(response).to redirect_to(root_path)
  end

  it "lets admins assign unassigned frames, which records a manual link and a command" do
    sign_in admin
    get unassigned_frames_path
    expect(response.body).to include("Andromeda?", "nearest target #{target.name}")

    post assign_frames_path, params: { target_id: target.id, telescope_id: telescope.id, night: "2026-09-24", object_header: "Andromeda?" }
    expect(unassigned.reload).to have_attributes(target: target, assignment_source: "manual")
    expect(node.processing_commands.find_by!(kind: "assign_frames").payload["sha256s"]).to eq([ unassigned.sha256 ])
  end

  it "doesn't let members use the unassigned inbox" do
    sign_in owner
    get unassigned_frames_path
    expect(response).to redirect_to(root_path)
  end

  it "shows nights and issues on the project page and turns controls into commands" do
    sign_in owner
    get project_path(project)
    expect(response.body).to include("Nights", "2026-09-24", "Ha flats needed", "Processing")

    post project_target_night_path(project, target), params: { night: "2026-09-24", filter: "Ha", include: "0" }
    post project_target_rerun_path(project, target), params: { filter: "Ha" }
    post project_target_rereference_path(project, target), params: { confirm: "1" }
    post project_target_mode_path(project, target), params: { mode: "frame_reintegration" }
    expect(node.processing_commands.pluck(:kind)).to eq(%w[night_exclude rerun rereference set_mode])
    expect(target.reload.effective_processing_settings.dig("multi_night", "mode")).to eq("frame_reintegration")

    patch project_target_settings_path(project, target), params: { drizzle_scale: "2", pin_to_nas: "1" }
    expect(flash[:notice]).to include("re-reference")
    expect(target.reload.effective_processing_settings).to include("drizzle_scale" => 2, "pin_to_nas" => true)
  end

  it "requires confirming a re-reference" do
    sign_in owner
    post project_target_rereference_path(project, target)
    expect(node.processing_commands).to be_empty
  end

  it "lets the owner waive an issue and admins approve fetches" do
    sign_in owner
    get issue_path(issue)
    expect(response.body).to include("FLAT_MISSING")
    post waive_issue_path(issue), params: { note: "Using sky flats" }
    expect(node.processing_commands.find_by!(kind: "issue_waive").payload).to include("fingerprint" => "FLAT_MISSING:x")

    fetch = ProcessingIssue.create!(processing_node: node, fingerprint: "FETCH_APPROVAL:1", kind: "FETCH_APPROVAL_REQUIRED",
                                    severity: "blocking", message: "31 GB from Deep Archive", opened_at: Time.current)
    post approve_issue_path(fetch)
    expect(node.processing_commands.where(kind: "approve_fetch")).to be_empty # owner isn't an admin
    sign_in admin
    post approve_issue_path(fetch)
    expect(node.processing_commands.where(kind: "approve_fetch").count).to eq(1)
  end

  it "manages processing nodes and their keys" do
    sign_in admin
    post admin_processing_nodes_path, params: { processing_node: { name: "altair-proc-02", telescope_ids: [ telescope.id ] } }
    created = ProcessingNode.find_by!(name: "altair-proc-02")
    expect(created.telescopes).to eq([ telescope ])

    post create_key_admin_processing_node_path(created), params: { name: "PC key" }
    key = created.api_keys.sole
    expect(key.scopes).to include("frames:write")
    get admin_processing_node_path(created)
    expect(response.body).to include("PC key")

    post refresh_config_admin_processing_node_path(created)
    expect(created.processing_commands.sole.kind).to eq("refresh_config")
    post revoke_key_admin_processing_node_path(created, key_id: key.id)
    expect(key.reload).not_to be_active
  end

  it "shows the optical train page and logs equipment events as commands" do
    CalibrationMaster.create!(processing_node: node, altair_id: 1, optical_train: train, kind: "flat", filter: "Ha", n_frames: 30, sha256: "x" * 64)
    sign_in admin
    get telescope_optical_train_path(telescope, train)
    expect(response.body).to include("Ha flats needed", "Calibration library", "flat")

    post admin_telescope_optical_train_equipment_events_path(telescope, train), params: { kind: "sensor_cleaned", note: "dust" }
    event = train.equipment_events.sole
    expect(node.processing_commands.find_by!(kind: "equipment_event").payload).to eq("equipment_event_id" => event.id)
  end

  it "shows node health and issues on the dashboards" do
    node.update!(last_heartbeat_at: 5.minutes.ago, status: { "status" => { "nas_free_pct" => 41 } })
    sign_in admin
    get root_path
    expect(response.body).to include(node.name, "NAS 41% free")
    get admin_root_path
    expect(response.body).to include("Unassigned frames (1)")
    sign_in owner
    get root_path
    expect(response.body).to include("Needs your attention", "Ha flats needed")
  end
end
