require "rails_helper"

RSpec.describe "Pausing, resuming and adding to targets", type: :request do
  let(:owner) { create(:user) }
  let(:telescope) { create(:telescope) }
  let!(:train) { create(:optical_train, telescope: telescope, key: "esprit", filters: [ { "name" => "Ha" }, { "name" => "OIII" } ]) }
  let(:project) { create(:project, user: owner) }
  let(:target) { create(:target, user: owner, project: project, telescope: telescope, optical_train: train, status: :active) }
  let!(:plan) { create(:exposure_plan, target: target, filter: "Ha", desired_count: 10, completed_count: 4) }

  def active_target_ids
    @key ||= create(:api_key, telescope: telescope)
    get "/api/v1/telescopes/#{telescope.slug}/active_targets", headers: { "Authorization" => "Bearer #{@key.plaintext_token}" }
    response.parsed_body["targets"].map { |t| t["id"] }
  end

  before { sign_in owner }

  it "pauses a target off the telescope's list and resumes it" do
    expect(active_target_ids).to eq([ target.id ])

    post pause_target_path(target)
    expect(response).to redirect_to(target_path(target))
    expect(target.reload).to be_paused
    expect(active_target_ids).to be_empty

    get target_path(target)
    expect(response.body).to include("Paused", "Resume")

    post resume_target_path(target)
    expect(target.reload).not_to be_paused
    expect(active_target_ids).to eq([ target.id ])
  end

  it "pauses and resumes a whole project" do
    post pause_project_path(project)
    expect(project.reload).to be_paused
    expect(active_target_ids).to be_empty
    get project_path(project)
    expect(response.body).to include("Resume project", "Project paused")

    post resume_project_path(project)
    expect(project.reload).to be_active
    expect(active_target_ids).to eq([ target.id ])
  end

  it "doesn't let another member pause someone else's target" do
    sign_in create(:user)
    post pause_target_path(target)
    expect(target.reload).not_to be_paused
  end

  describe "exposure plans" do
    it "raises a count, adds a filter and removes an untouched plan" do
      untouched = create(:exposure_plan, target: target, filter: "OIII", exposure_seconds: 600, desired_count: 5)
      get edit_target_plans_path(target)
      expect(response.body).to include("Add a plan")

      patch target_plans_path(target), params: { target: {
        plans: { plan.id.to_s => { desired_count: "30" }, untouched.id.to_s => { desired_count: "5", remove: "1" } },
        new_plan: { filter: "oiii", exposure_seconds: "300", desired_count: "12" }
      } }

      expect(response).to redirect_to(target_path(target))
      plans = target.exposure_plans.reload.order(:id)
      expect(plans.map(&:to_s)).to eq([ "Ha 300s x30", "OIII 300s x12" ])
      expect(ExposurePlan.exists?(untouched.id)).to be(false)
      expect(target.target_events.last.payload).to include("status" => "plans_updated")
    end

    it "won't remove a plan with captured frames" do
      patch target_plans_path(target), params: { target: { plans: { plan.id.to_s => { desired_count: "10", remove: "1" } } } }
      expect(response).to have_http_status(:unprocessable_content)
      expect(plan.reload).to be_present
    end

    it "rejects a filter the optical train doesn't have, changing nothing" do
      patch target_plans_path(target), params: { target: {
        plans: { plan.id.to_s => { desired_count: "50" } }, new_plan: { filter: "Sii", exposure_seconds: "300", desired_count: "5" }
      } }
      expect(response).to have_http_status(:unprocessable_content)
      expect(plan.reload.desired_count).to eq(10)
    end

    it "reopens a completed target when more frames are asked for" do
      plan.update!(completed_count: 10)
      target.update!(status: :completed)
      get target_path(target)
      expect(response.body).to include("Add frames")

      patch target_plans_path(target), params: { target: { plans: { plan.id.to_s => { desired_count: "20" } } } }
      expect(target.reload).to be_active
      expect(active_target_ids).to eq([ target.id ])
    end
  end

  describe "adding a target to an existing project" do
    let!(:m42) { create(:astro_object, primary_name: "Orion Nebula", ra_deg: 83.82, dec_deg: -5.39) }

    it "runs the wizard in add mode and appends the target" do
      target # the project's first target
      get new_project_path(project_id: project.id)
      expect(response.body).to include("Adding targets to", project.name)

      post project_wizard_add_object_path, params: { astro_object_id: m42.id }
      patch new_project_path
      patch project_wizard_telescope_path, params: { optical_train_id: train.id }
      post project_wizard_add_exposure_plan_path, params: { filter: "Ha", exposure_seconds: 300, desired_count: 20 }
      patch project_wizard_exposures_path
      get project_wizard_review_path
      expect(response.body).to include("Add to #{project.name}")

      expect { post project_wizard_create_path }.to change(Target, :count).by(1).and change(Project, :count).by(0)
      expect(response).to redirect_to(project_path(project))
      added = project.targets.order(:id).last
      expect(added).to have_attributes(name: "Orion Nebula", is_primary: false, status: "submitted", user: owner)
      expect(added.exposure_plans.map(&:to_s)).to eq([ "Ha 300s x20" ])

      get new_project_path(new: 1)
      expect(response.body).not_to include("Adding targets to")
    end

    it "is refused for someone else's project" do
      sign_in create(:user)
      get new_project_path(project_id: project.id)
      expect(response).to redirect_to(root_path)
    end
  end
end
