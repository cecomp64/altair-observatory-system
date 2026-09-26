require "rails_helper"

# Every page renders for the people who can see it.
RSpec.describe "Pages", type: :request do
  let(:admin) { create(:user, :admin) }
  let(:member) { create(:user) }
  let(:telescope) { create(:telescope) }
  let!(:train) { create(:optical_train, telescope: telescope) }
  let(:project) { create(:project, user: member) }
  let!(:target) { create(:target, project: project, user: member, telescope: telescope, optical_train: train) }

  before { create(:exposure_plan, target: target, filter: "Ha") }

  it "renders the member pages" do
    sign_in member
    [ root_path, projects_path, projects_path(scope: "club"), project_path(project), edit_project_path(project),
      targets_path, target_path(target), telescopes_path, telescope_path(telescope), new_project_path,
      objects_path, new_object_path ].each do |path|
      get path
      expect(response).to have_http_status(:ok), "#{path} → #{response.status}"
    end
  end

  it "shows a target's masters and uses the latest multi-night master as its preview" do
    master = create(:data_product, target: target, kind: :multi_night_master, filter: "Ha", version: 3)
    master.preview.attach(io: StringIO.new("\xFF\xD8\xFF\xE0jpeg".b), filename: "p.jpg", content_type: "image/jpeg")
    sign_in member

    get target_path(target)

    expect(response.body).to include("Multi night master", "v3", "Ha master")
    expect(response.body).not_to include("No preview yet")
  end

  it "shows tonight's targets on the dashboard and project visibility" do
    sign_in member
    travel_to Time.zone.parse("2026-09-24 20:00 PDT") do
      get root_path
      expect(response.body).to include("Tonight at", telescope.name, target.name)
      get project_path(project)
      expect(response.body).to include("Visibility tonight at #{telescope.name}")
    end
  end

  it "renders the admin pages" do
    sign_in admin
    [ admin_root_path, admin_telescopes_path, admin_telescope_path(telescope), edit_admin_telescope_path(telescope),
      new_admin_telescope_optical_train_path(telescope), edit_admin_telescope_optical_train_path(telescope, train),
      admin_catalogue_path ].each do |path|
      get path
      expect(response).to have_http_status(:ok), "#{path} → #{response.status}"
    end
  end

  it "lets an admin create an optical train with filters" do
    sign_in admin
    post admin_telescope_optical_trains_path(telescope), params: { optical_train: {
      key: "rasa8_533mc", name: "RASA 8 + ASI533MC", camera_type: "osc", focal_length_mm: 400,
      filters_text: "OSC\nL-eNhance: LeNhance", header_aliases_camera: "ZWO ASI533MC Pro"
    } }
    train = telescope.optical_trains.find_by!(key: "rasa8_533mc")
    expect(train.filter_names).to eq([ "OSC", "L-eNhance" ])
    expect(train.header_aliases).to eq("telescope" => [], "camera" => [ "ZWO ASI533MC Pro" ])
  end

  it "queues a catalogue import from the admin page" do
    sign_in admin
    expect { post import_admin_catalogue_path(catalogue: "openngc") }.to have_enqueued_job(CatalogueImportJob).with("openngc")
  end

  it "hides another member's private project" do
    sign_in create(:user)
    get project_path(project)
    expect(response).to redirect_to(root_path)
  end

  it "shows a club project to other members" do
    project.update!(visibility: "club")
    sign_in create(:user)
    get project_path(project)
    expect(response).to have_http_status(:ok)
  end
end
