require "rails_helper"

RSpec.describe "Dynamic catalogues in the catalogue", type: :request do
  let(:catalogue) { DynamicCatalogue.for("aavso_campaigns") }
  let!(:ch_cyg) { create(:astro_object, primary_name: "CH Cyg", source: "aavso", object_type: "Variable Star") }
  let!(:old) { create(:astro_object, primary_name: "RS Oph", source: "aavso", object_type: "Variable Star") }
  let!(:m31) { create(:astro_object, primary_name: "Andromeda Galaxy") }

  before do
    catalogue.entries.create!(astro_object: ch_cyg, first_seen_at: 2.days.ago, last_seen_at: 1.hour.ago,
                              details: { "star_name" => "CH Cyg", "var_type" => "ZAND+SR", "max_mag" => 5.6, "max_mag_band" => "V",
                                         "min_mag" => 10.1, "min_mag_band" => "V", "cadence_days" => 3.0,
                                         "campaigns" => [ { "id" => 880, "title" => "Flare Star Campaign", "start_date" => "2025-01-13", "end_date" => nil,
                                                            "data_types" => "Photometry", "forum_url" => "https://forums.aavso.org/t/880" } ],
                                         "other_info" => "See [[https://www.aavso.org/aavso-alert-notice-900 Alert Notice 900]] <b>now</b>" })
    catalogue.entries.create!(astro_object: old, first_seen_at: 30.days.ago, last_seen_at: 10.days.ago, removed_at: 9.days.ago)
  end

  it "filters to objects on a list, with a badge on each listed row" do
    sign_in create(:user)
    get objects_path
    expect(response.body).to include("AAVSO Alerts &amp; Campaigns", "Andromeda Galaxy")

    get objects_path(list: "aavso_campaigns")
    expect(response.body).to include("CH Cyg")
    expect(response.body).not_to include("RS Oph", "Andromeda Galaxy")
    expect(Nokogiri::HTML(response.body).css("tr[data-object-row='#{ch_cyg.id}'] span.bg-amber-100").text.strip).to eq("AAVSO")

    page = Nokogiri::HTML(response.body)
    expect(page.at_css("input[type=checkbox][name='list[]'][value='aavso_campaigns']")["checked"]).to be_present
    remove = page.css("a[title='Remove filter']").find { |link| link.text.include?("AAVSO Alerts & Campaigns") }
    expect(remove["href"]).not_to include("list")
  end

  it "shows the campaign on the object page, escaping the notes but linking the notice" do
    sign_in create(:user)
    get object_path(ch_cyg)
    expect(response.body).to include("On AAVSO Alerts &amp; Campaigns", "ZAND+SR", "5.6V – 10.1V", "&lt;b&gt;now&lt;/b&gt;")
    expect(response.body).to include("Flare Star Campaign", "since January 13, 2025, ongoing", "https://apps.aavso.org/v2/campaigns/880")
    link = Nokogiri::HTML(response.body).at_css("a[href='https://www.aavso.org/aavso-alert-notice-900']")
    expect(link.text).to eq("Alert Notice 900")

    get object_path(old)
    expect(response.body).to include("Was on AAVSO Alerts &amp; Campaigns")
    expect(response.body).not_to include("On AAVSO Alerts &amp; Campaigns")
  end

  it "lets an admin refresh a configured list now" do
    sign_in create(:user, :admin)
    allow(Catalogue::Dynamic::AavsoCampaigns).to receive(:configured?).and_return(true)
    get admin_catalogue_path
    expect(response.body).to include("Dynamic catalogues", "AAVSO Alerts &amp; Campaigns")

    expect { post refresh_admin_catalogue_path(key: "aavso_campaigns") }.to have_enqueued_job(DynamicCatalogueRefreshJob).with("aavso_campaigns")
    expect { post refresh_admin_catalogue_path(key: "nope") }.not_to have_enqueued_job(DynamicCatalogueRefreshJob)
  end

  it "tells an admin how to configure a list without a key" do
    sign_in create(:user, :admin)
    allow(Catalogue::Dynamic::AavsoCampaigns).to receive(:configured?).and_return(false)
    get admin_catalogue_path
    expect(response.body).to include("Not configured", "AAVSO_API_KEY")
  end
end
