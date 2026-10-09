require "rails_helper"

RSpec.describe "Objects", type: :request do
  let(:user) { create(:user) }
  let!(:telescope) { create(:telescope, latitude: 37.34, longitude: -121.89) }
  let!(:m31) do
    create(:astro_object, primary_name: "Andromeda Galaxy", ra_deg: 10.68479, dec_deg: 41.26906, magnitude: 3.4,
                          size_major_arcmin: 190, constellation: "And", object_type: "Galaxy", source: "openngc").tap { |o| o.add_alias("M 31", catalog: "Messier") }
  end
  let!(:lmc) { create(:astro_object, primary_name: "Large Magellanic Cloud", ra_deg: 80.89, dec_deg: -69.76, object_type: "Galaxy") }

  before { sign_in user }

  it "searches by alias and filters by facets" do
    get objects_path(q: "m31")
    expect(response.body).to include("Andromeda Galaxy")
    expect(response.body).not_to include("Large Magellanic Cloud")

    get objects_path(constellation: "And")
    expect(response.body).to include("Andromeda Galaxy")
  end

  it "filters to objects well placed tonight at a telescope" do
    travel_to Time.zone.parse("2026-09-24 20:00 PDT") do
      get objects_path(telescope: telescope.slug, tonight: "1", sort: "score")
    end
    expect(response.body).to include("Andromeda Galaxy")
    expect(response.body).not_to include("Large Magellanic Cloud")
  end

  it "shows tonight's chart, best viewing and a start-a-project button" do
    get object_path(m31, telescope: telescope.slug, date: "2026-09-24")
    expect(response).to have_http_status(:ok)
    expect(response.body).to include("Clear in darkness", "Best viewing from", "Start a project", "Path across the sky from #{telescope.name}")
    labels = Nokogiri::HTML(response.body).css("canvas[data-chart-data-value]").flat_map { |c| JSON.parse(c["data-chart-data-value"])["datasets"].map { |d| d["label"] } }
    expect(labels).to include("Minimum altitude (30.0°)")
    expect(labels.count("Andromeda Galaxy")).to eq(2) # the altitude chart and the sky path
  end

  it "starts a project with the object and the telescope it was viewed from" do
    post project_wizard_add_object_path, params: { astro_object_id: m31.id, telescope: telescope.slug, start: "target" }
    expect(response).to redirect_to(project_wizard_telescope_path(telescope: telescope.slug))
  end

  it "filters by several values of the same facet" do
    create(:astro_object, primary_name: "Pelican Nebula", ra_deg: 313.0, dec_deg: 44.0, object_type: "Nebula")
    get objects_path(type: %w[Nebula])
    expect(response.body).to include("Pelican Nebula")
    expect(response.body).not_to include("Andromeda Galaxy")

    get objects_path(type: %w[Nebula Galaxy])
    expect(response.body).to include("Pelican Nebula", "Andromeda Galaxy")
    expect(response.body).to include("3 objects")
  end

  it "only lets admins add objects to the catalogue" do
    get objects_path
    expect(response.body).not_to include("Add an object")
    expect { post objects_path, params: { astro_object: { primary_name: "My Field", ra: "10:00:00", dec: "+20:00:00" } } }
      .not_to change(AstroObject, :count)
    get new_object_path
    expect(response).to redirect_to(root_path)
  end

  it "adds a custom object with aliases (admins only)" do
    user.update!(role: :admin)
    post objects_path, params: { astro_object: { primary_name: "My Field", ra: "10:00:00", dec: "+20:00:00", aliases: "Field A, FA-1" } }
    object = AstroObject.find_by!(primary_name: "My Field")
    expect(response).to redirect_to(object_path(object))
    expect(object).to have_attributes(source: "custom", created_by: user, shared: false)
    expect(object.ra_deg.to_f).to eq(150.0)
    expect(object.alias_names).to include("Field A", "FA-1")
  end

  it "rejects a custom object without valid coordinates" do
    user.update!(role: :admin)
    post objects_path, params: { astro_object: { primary_name: "Nowhere", ra: "x", dec: "y" } }
    expect(response).to have_http_status(:unprocessable_content)
  end

  it "uploads a showcase and queues a survey fetch" do
    image = Rack::Test::UploadedFile.new(StringIO.new("\xFF\xD8\xFF\xE0jpeg".b), "image/jpeg", original_filename: "m31.jpg")
    post object_showcase_path(m31), params: { image: image }
    expect(m31.reload.showcase.image).to be_attached

    expect { post object_showcase_path(lmc), params: { survey: "DSS2 Red" } }
      .to have_enqueued_job(ShowcaseSurveyFetchJob).with(lmc.id, "DSS2 Red")
    # A member can't replace a showcase someone else already set.
    expect { post object_showcase_path(m31), params: { survey: "DSS2 Red" } }.not_to have_enqueued_job
  end

  it "refuses non-image uploads" do
    file = Rack::Test::UploadedFile.new(StringIO.new("<svg/>"), "image/svg+xml", original_filename: "x.svg")
    post object_showcase_path(m31), params: { image: file }
    expect(flash[:alert]).to include("JPEG or PNG")
  end
end

RSpec.describe ShowcaseSurveyFetchJob do
  it "stores the SkyView JPEG as the object's showcase" do
    object = create(:astro_object, size_major_arcmin: 60)
    stubs = Faraday::Adapter::Test::Stubs.new
    stubs.get("/current/cgi/runquery.pl") do |env|
      expect(env.params).to include("Survey" => "DSS2 Red", "Return" => "JPEG", "Size" => "2.0")
      [ 200, { "Content-Type" => "image/jpeg" }, "\xFF\xD8\xFF\xE0jpeg".b ]
    end

    described_class.perform_now(object.id, "DSS2 Red", connection: Faraday.new { |f| f.adapter :test, stubs })

    expect(object.reload.showcase).to have_attributes(source_type: "survey", survey_name: "DSS2 Red")
    expect(object.showcase.image).to be_attached
  end
end
