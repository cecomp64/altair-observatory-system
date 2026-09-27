require "rails_helper"

RSpec.describe "ProjectWizard", type: :request do
  let(:user) { create(:user) }
  let(:telescope) { create(:telescope) }
  let!(:train) { create(:optical_train, telescope: telescope, key: "esprit100_2600mm") }
  let!(:m31) do
    create(:astro_object, primary_name: "Andromeda Galaxy", ra_deg: 10.68479, dec_deg: 41.26906).tap do |o|
      o.add_alias("M 31", catalog: "Messier")
      o.add_alias("NGC 224", catalog: "NGC")
    end
  end

  before { sign_in user }

  def choose_train_and_plan
    patch new_project_path
    expect(response).to redirect_to(project_wizard_telescope_path)
    patch project_wizard_telescope_path, params: { optical_train_id: train.id }
    expect(response).to redirect_to(project_wizard_exposures_path)
    post project_wizard_add_exposure_plan_path, params: { filter: "h-alpha", exposure_seconds: 300, desired_count: 20 }
    patch project_wizard_exposures_path
    expect(response).to redirect_to(project_wizard_review_path)
  end

  it "walks objects -> telescope -> exposures -> review and creates a project with one target per object" do
    get new_project_path(q: "m31")
    expect(response.body).to include("Andromeda Galaxy")

    post project_wizard_add_object_path, params: { astro_object_id: m31.id }
    post project_wizard_add_object_path, params: { name: "Panel B", ra: "00:45:00", dec: "+41:30:00", panel: "B" }
    choose_train_and_plan

    expect {
      post project_wizard_create_path, params: { name: "Andromeda mosaic", priority: 3 }
    }.to change(Project, :count).by(1).and change(Target, :count).by(2)

    project = Project.order(:created_at).last
    expect(response).to redirect_to(project_path(project))
    expect(project).to have_attributes(name: "Andromeda mosaic", priority: 3, user: user, status: "active")

    first, second = project.targets.order(:id)
    expect(first).to have_attributes(astro_object: m31, name: "Andromeda Galaxy", optical_train: train, is_primary: true)
    expect(first).to be_submitted
    expect(second).to have_attributes(panel: "B", is_primary: false)
    # A custom target becomes the member's own private object.
    expect(second.astro_object).to have_attributes(primary_name: "Panel B", source: "custom", created_by: user, shared: false)
    expect(second.ra_deg.to_f).to be_within(0.001).of(11.25)
    # The filter was entered as an alias and stored as the train's canonical name.
    expect(first.exposure_plans.sole).to have_attributes(filter: "Ha", exposure_seconds: 300, desired_count: 20)
  end

  it "offers only the optical train's filters" do
    post project_wizard_add_object_path, params: { astro_object_id: m31.id }
    patch new_project_path
    patch project_wizard_telescope_path, params: { optical_train_id: train.id }

    get project_wizard_exposures_path
    expect(response.body).to include("<option value=\"OIII\">")

    post project_wizard_add_exposure_plan_path, params: { filter: "SII", exposure_seconds: 300, desired_count: 5 }
    expect(flash[:alert]).to include("choose a filter")
  end

  it "resolves an unknown name through Telescopius" do
    client = instance_double(Catalogue::TelescopiusClient)
    allow(Catalogue::TelescopiusClient).to receive_messages(configured?: true, new: client)
    allow(client).to receive(:search).with("Pacman Nebula").and_return(
      Catalogue::TelescopiusClient::Result.new(name: "Pacman Nebula", ra_deg: 13.2, dec_deg: 56.6, aliases: [ "NGC 281" ])
    )

    post project_wizard_add_object_path, params: { resolve: "Pacman Nebula" }

    # A member's lookup adds nothing to the catalogue...
    expect(flash[:notice]).to eq("Added Pacman Nebula.")
    expect(AstroObject.find_by_alias("NGC281")).to be_nil

    # ...until the project is created: then it is their own private object,
    # reused by their next project.
    choose_train_and_plan
    post project_wizard_create_path, params: { name: "Pacman" }
    object = AstroObject.find_by_alias("NGC281")
    expect(object).to have_attributes(primary_name: "Pacman Nebula", source: "custom", source_ref: "telescopius", created_by: user, shared: false)
    expect(Project.last.targets.sole.astro_object).to eq(object)

    post project_wizard_add_object_path, params: { name: "Pacman Nebula", ra: "00h 52m 48s", dec: "+56° 36′ 00″" }
    choose_train_and_plan
    expect { post project_wizard_create_path, params: { name: "Pacman again" } }.not_to change(AstroObject, :count)
    expect(Project.last.targets.sole.astro_object).to eq(object)
  end

  it "stores an admin's lookup in the shared catalogue" do
    sign_in create(:user, :admin)
    client = instance_double(Catalogue::TelescopiusClient)
    allow(Catalogue::TelescopiusClient).to receive_messages(configured?: true, new: client)
    allow(client).to receive(:search).and_return(
      Catalogue::TelescopiusClient::Result.new(name: "Pacman Nebula", ra_deg: 13.2, dec_deg: 56.6, aliases: [ "NGC 281" ])
    )
    post project_wizard_add_object_path, params: { resolve: "Pacman Nebula" }
    expect(AstroObject.find_by_alias("NGC281")).to have_attributes(source: "telescopius")
  end

  it "keeps the custom objects out of the catalogue if the project doesn't save" do
    post project_wizard_add_object_path, params: { name: "Lost Field", ra: "10:00:00", dec: "+20:00:00" }
    choose_train_and_plan
    allow_any_instance_of(Project).to receive(:save).and_return(false) # rubocop:disable RSpec/AnyInstance
    expect { post project_wizard_create_path, params: { name: "Nope" } }.not_to change(AstroObject, :count)
  end

  it "refuses to continue past objects with nothing chosen" do
    get project_wizard_telescope_path
    expect(response).to redirect_to(new_project_path)
  end

  it "rejects an invalid custom coordinate" do
    post project_wizard_add_object_path, params: { name: "Bad", ra: "not a number", dec: "0" }
    expect(flash[:alert]).to include("valid right ascension")
  end

  it "redirects the old /targets/new entry point" do
    get "/targets/new"
    expect(response).to redirect_to("/projects/new")
  end
end
