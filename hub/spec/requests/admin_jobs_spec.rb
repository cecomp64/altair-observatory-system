require "rails_helper"

RSpec.describe "Admin jobs", type: :request do
  let(:admin) { create(:user, :admin) }
  let(:member) { create(:user) }
  let(:node) { create(:processing_node) }
  let(:other_node) { create(:processing_node) }
  let(:target) { create(:target) }

  describe "processing jobs" do
    let!(:stack) do
      create(:processing_job, processing_node: node, target: target, night: "2026-09-24", filter: "Ha",
                              started_at: 2.hours.ago, finished_at: 1.hour.ago)
    end
    let!(:failed) do
      create(:processing_job, processing_node: other_node, kind: "MERGE", status: "failed", filter: "OIII",
                              error: "PixInsight exited with code 3\nTraceback line")
    end
    let!(:running) { create(:processing_job, processing_node: node, kind: "CALIB_MASTER", status: "running", filter: "SII", started_at: 5.minutes.ago) }

    before { sign_in admin }

    it "lists every node's jobs with status counts" do
      get admin_processing_jobs_path
      expect(response.body).to include(target.name, "2026-09-24", node.name, other_node.name, "PixInsight exited with code 3")
      expect(response.body).to include("All (3)", "Active (1)", "Failed (1)", "Succeeded (1)", "Blocked (0)")
      expect(response.body).not_to include("Traceback line")
    end

    it "filters by status, kind and node" do
      get admin_processing_jobs_path(status: "failed")
      expect(response.body).to include("OIII")
      expect(response.body).not_to include("SII")
      get admin_processing_jobs_path(status: "active")
      expect(response.body).to include("SII")
      expect(response.body).not_to include("OIII")
      get admin_processing_jobs_path(kind: "MERGE")
      expect(response.body).to include("OIII", "All (1)")
      expect(response.body).not_to include("SII")
      get admin_processing_jobs_path(node: node.name)
      expect(response.body).to include("SII", "All (2)")
      expect(response.body).not_to include("OIII")
      get admin_processing_jobs_path(status: "bogus", kind: "bogus", node: "bogus")
      expect(response.body).to include("SII", "OIII", "All (3)")
    end

    it "shows a job with its full error" do
      get admin_processing_job_path(failed)
      expect(response.body).to include("Merge", "##{failed.altair_id}", "Failed", "Traceback line", other_node.name)
    end

    it "links to the jobs from the node page and the admin dashboard" do
      get admin_processing_node_path(node)
      expect(response.body).to include(admin_processing_jobs_path(node: node.name), admin_processing_job_path(running))
      get admin_root_path
      expect(response.body).to include(admin_processing_jobs_path, mission_control_jobs_path)
    end

    it "is for admins only" do
      sign_in member
      get admin_processing_jobs_path
      expect(response).to redirect_to(root_path)
      get admin_processing_job_path(stack)
      expect(response).to redirect_to(root_path)
    end
  end

  describe "background queue (Mission Control)" do
    it "is served to admins without HTTP basic auth" do
      sign_in admin
      get mission_control_jobs_path
      follow_redirect! while response.redirect?
      expect(response).to have_http_status(:ok)
      expect(response.body).to include("Queues")
    end

    it "sends other users back to the dashboard" do
      sign_in member
      get mission_control_jobs_path
      expect(response).to redirect_to("/") # the Hub's root, not the engine's
    end

    it "asks visitors to sign in" do
      get mission_control_jobs_path
      expect(response).to redirect_to(new_user_session_path)
    end
  end
end
