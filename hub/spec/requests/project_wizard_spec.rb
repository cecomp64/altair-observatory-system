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

  def choose_train(next_step = project_wizard_objects_path)
    patch new_project_path, params: { optical_train_id: train.id }
    expect(response).to redirect_to(next_step)
  end

  # Adds the objects (if any) after the telescope, as when starting with one.
  def choose_train_and_plan
    post project_wizard_start_path, params: { start: "telescope" }
    choose_train
    patch project_wizard_objects_path
    expect(response).to redirect_to(project_wizard_exposures_path)
    post project_wizard_add_exposure_plan_path, params: { filter: "h-alpha", exposure_seconds: 300, desired_count: 20 }
    patch project_wizard_exposures_path
    expect(response).to redirect_to(project_wizard_review_path)
  end

  def chart_datasets
    canvas = Nokogiri::HTML(response.body).at_css("canvas[data-horizon-preview-target=chart]")
    JSON.parse(canvas["data-chart-data-value"])["datasets"]
  end

  it "asks whether to start with a target or a telescope" do
    get new_project_path
    expect(response.body).to include("Start with a target", "Start with a telescope")
    post project_wizard_start_path, params: { start: "sideways" }
    expect(response).to redirect_to(new_project_path)
  end

  it "walks telescope -> objects -> exposures -> review and creates a project with one target per object" do
    post project_wizard_start_path, params: { start: "telescope" }
    expect(response).to redirect_to(project_wizard_telescope_path)
    get project_wizard_telescope_path
    expect(response.body).to include(telescope.name, train.name, "Flat horizon")
    choose_train

    get project_wizard_objects_path(q: "m31")
    expect(response.body).to include("Andromeda Galaxy")

    post project_wizard_add_object_path, params: { astro_object_id: m31.id }
    expect(response).to redirect_to(project_wizard_objects_path)
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

  it "walks target -> telescope -> exposures -> review, charting the objects at every telescope" do
    other = create(:telescope, name: "Southern Scope", latitude: -31.27, longitude: 149.06)
    create(:optical_train, telescope: other, key: "southern_train")

    post project_wizard_start_path, params: { start: "target" }
    expect(response).to redirect_to(project_wizard_objects_path)
    get project_wizard_objects_path(q: "m31")
    expect(response).to have_http_status(:ok)
    expect(response.body).to include("Andromeda Galaxy", "compare how each telescope sees them tonight")
    expect(response.body).not_to include("<canvas", "Change telescope")

    # The telescopes come after the objects.
    get project_wizard_telescope_path
    expect(response).to redirect_to(project_wizard_objects_path)
    post project_wizard_add_object_path, params: { astro_object_id: m31.id, q: "m31" }
    expect(response).to redirect_to(project_wizard_objects_path)
    patch project_wizard_objects_path
    expect(response).to redirect_to(project_wizard_telescope_path)

    travel_to Time.utc(2026, 10, 1, 20) # autumn, when Andromeda is high in the north
    get project_wizard_telescope_path
    page = Nokogiri::HTML(response.body)
    expect(page.css("[data-telescope-view] canvas").size).to eq(2)
    expect(response.body).to include("Andromeda Galaxy:", "Night of", "Southern Scope")
    expect(response.body).not_to include("Flat horizon")
    # Andromeda is far north: only the northern telescope sees it in darkness.
    best = page.at_xpath("//span[contains(., 'Best view tonight')]/ancestor::div[contains(@class, 'rounded-xl')][1]")
    expect(best.text).to include(telescope.name)
    travel_back

    choose_train(project_wizard_exposures_path)
    get project_wizard_exposures_path
    expect(response.body).to include(%(href="#{project_wizard_telescope_path}"))
    post project_wizard_add_exposure_plan_path, params: { filter: "Ha", exposure_seconds: 300, desired_count: 20 }
    patch project_wizard_exposures_path
    expect(response).to redirect_to(project_wizard_review_path)

    expect { post project_wizard_create_path, params: { name: "Andromeda" } }.to change(Target, :count).by(1)
    expect(Project.last.targets.sole).to have_attributes(astro_object: m31, optical_train: train)
  end

  it "charts tonight's altitude against the horizon, with search results as hidden previews" do
    telescope.update!(min_altitude_deg: 5, horizon_file: Rack::Test::UploadedFile.new(StringIO.new("0,10\n180,20\n"), "text/csv", original_filename: "horizon.csv"))
    choose_train
    get project_wizard_objects_path
    expect(response.body).to include("Search for an object to see how it clears #{telescope.name}'s horizon tonight.")
    expect(response.body).not_to include("<canvas")

    post project_wizard_add_object_path, params: { astro_object_id: m31.id }
    get project_wizard_objects_path(q: "andromeda")
    expect(response.body).to include("Night of", telescope.name, "Change telescope", "Local time (")
    datasets = chart_datasets
    expect(datasets.map { |d| d["label"] }).to match([ "Andromeda Galaxy", "Limit (horizon / min altitude)", "Andromeda Galaxy", "Andromeda Galaxy limit", a_string_starting_with("Moon") ])
    altitude, limit, preview, preview_limit = datasets
    expect(altitude).not_to have_key("hidden")
    # The limit follows the horizon in the object's direction, never below the minimum altitude.
    expect(limit["data"].minmax).to match([ be >= 5, be <= 20 ])
    expect(limit["data"].uniq.size).to be > 1
    expect([ preview, preview_limit ]).to all(include("hidden" => true, "hideInLegend" => true, "previewKey" => "object-#{m31.id}"))
    expect(response.body).to include(%(data-horizon-preview-key-param="object-#{m31.id}"))
    expect(response.body).to match(/h clear tonight|Not clear of the horizon/)
  end

  it "gives each object its own limit when there are several" do
    choose_train
    post project_wizard_add_object_path, params: { astro_object_id: m31.id }
    post project_wizard_add_object_path, params: { name: "Southern field", ra: "05:35:17", dec: "-05:23:28" }

    get project_wizard_objects_path
    datasets = chart_datasets
    expect(datasets.map { |d| d["label"] }).to match([ "Andromeda Galaxy", "Southern field", "Andromeda Galaxy limit", "Southern field limit",
                                                       "Limits (horizon / min altitude, dashed)", a_string_starting_with("Moon") ])
    expect(datasets[2..3]).to all(include("hideInLegend" => true))
    expect(datasets[2]["borderColor"]).to eq(datasets[0]["borderColor"])
    expect(datasets[4]["data"]).to be_empty
  end

  it "asks for the telescope before the objects" do
    get project_wizard_objects_path
    expect(response).to redirect_to(project_wizard_telescope_path)
    expect(flash[:alert]).to eq("Pick a telescope first.")
  end

  it "starts with the target, and shows the telescopes next, when started from an object's page" do
    post project_wizard_add_object_path, params: { astro_object_id: m31.id, start: "target" }
    expect(response).to redirect_to(project_wizard_telescope_path)
    expect(flash[:notice]).to eq("Added Andromeda Galaxy. Choose a telescope to image it with.")
    get project_wizard_telescope_path
    expect(response.body).to include("Andromeda Galaxy:")

    choose_train(project_wizard_exposures_path)
    get project_wizard_objects_path
    expect(chart_datasets.map { |d| d["label"] }).to include("Andromeda Galaxy")
  end

  it "starts with the telescope, its default train preselected, when started from its page" do
    get new_project_path(telescope: telescope.slug)
    expect(response).to redirect_to(project_wizard_telescope_path(telescope: telescope.slug))
    follow_redirect!
    checked = Nokogiri::HTML(response.body).css("input[name=optical_train_id][checked]").map { |i| i["value"].to_i }
    expect(checked).to eq([ telescope.default_optical_train_id ])
  end

  it "adds a repeated filter and exposure to the existing row" do
    post project_wizard_add_object_path, params: { astro_object_id: m31.id }
    choose_train(project_wizard_exposures_path)
    post project_wizard_add_exposure_plan_path, params: { filter: "Ha", exposure_seconds: 300, desired_count: 20 }
    post project_wizard_add_exposure_plan_path, params: { filter: "Ha", exposure_seconds: 600, desired_count: 5 }
    post project_wizard_add_exposure_plan_path, params: { filter: "h-alpha", exposure_seconds: 300, desired_count: 10 }
    expect(flash[:notice]).to eq("Added 10 to Ha 300s (now 30).")

    get project_wizard_review_path
    rows = Nokogiri::HTML(response.body).css("li").map { |li| li.text.squish }
    expect(rows).to include("Ha · 300s x30", "Ha · 600s x5")
    expect(rows.count { |r| r.start_with?("Ha · 300s") }).to eq(1)
  end

  it "offers only the optical train's filters" do
    post project_wizard_add_object_path, params: { astro_object_id: m31.id }
    patch new_project_path, params: { optical_train_id: train.id }

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
    expect(flash[:notice]).to start_with("Added Pacman Nebula.")
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
    post project_wizard_start_path, params: { start: "telescope" }
    choose_train
    patch project_wizard_objects_path
    expect(response).to redirect_to(project_wizard_objects_path)
    get project_wizard_exposures_path
    expect(response).to redirect_to(project_wizard_objects_path)
  end

  it "refuses to continue past the telescope with nothing chosen" do
    patch new_project_path
    expect(response).to have_http_status(:unprocessable_content)
    expect(response.body).to include("Please choose a telescope to continue.")
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
