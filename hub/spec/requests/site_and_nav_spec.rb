require "rails_helper"

RSpec.describe "Telescope local time, coordinates and navigation", type: :request do
  include ActiveSupport::Testing::TimeHelpers

  let(:user) { create(:user) }
  let!(:telescope) { create(:telescope, name: "Backyard 16in", latitude: 37.3, longitude: -121.9, timezone: "America/Los_Angeles") }

  before do
    sign_in user
    travel_to Time.utc(2026, 9, 25, 6, 30)
  end

  after { travel_back }

  it "shows the telescope's local time next to its site in the same formats everywhere" do
    [ telescopes_path, telescope_path(telescope), observatory_path ].each do |path|
      get path
      expect(response.body).to include("23:30 PDT"), path
      expect(response.body).to include('data-local-clock-zone-value="America/Los_Angeles"'), path
    end
    get telescope_path(telescope)
    expect(response.body).to include("37° 18′ 00″ N", "121° 54′ 00″ W")
  end

  it "shows target coordinates in hours and degrees" do
    target = create(:target, user: user, telescope: telescope, ra_deg: 83.822, dec_deg: -5.391)
    get target_path(target)
    expect(response.body).to include("05h 35m 17.3s", "−05° 23′ 28″", 'title="83.82200°"')
  end

  it "lets admins enter a site in degrees, minutes and seconds" do
    admin = create(:user, :admin)
    sign_in admin
    patch admin_telescope_path(telescope), params: { telescope: { latitude: "33° 52′ 12″ S", longitude: "151:12:36" } }
    expect(telescope.reload.latitude.to_f).to be_within(0.0001).of(-33.87)
    expect(telescope.longitude.to_f).to be_within(0.0001).of(151.21)

    patch admin_telescope_path(telescope), params: { telescope: { latitude: "north-ish" } }
    expect(response).to have_http_status(:unprocessable_content)
  end

  it "lets admins upload a horizon file and reports a bad one" do
    sign_in create(:user, :admin)
    upload = ->(text) { Rack::Test::UploadedFile.new(StringIO.new(text), "text/csv", original_filename: "horizon.csv") }

    patch admin_telescope_path(telescope), params: { telescope: { horizon_file: upload.("azimuth,altitude\n0,15\n180,25\n") } }
    expect(response).to redirect_to(admin_telescope_path(telescope))
    expect(telescope.reload.horizon_points).to eq([ [ 0.0, 15.0 ], [ 180.0, 25.0 ] ])

    patch admin_telescope_path(telescope), params: { telescope: { horizon_file: upload.("0,15\n180,95\n") } }
    expect(response).to have_http_status(:unprocessable_content)
    expect(response.body).to include("Horizon file line 2: altitude must be between -90 and 90")
    expect(telescope.reload.horizon_points).to eq([ [ 0.0, 15.0 ], [ 180.0, 25.0 ] ])
  end

  it "has a compact nav with a More menu and a hamburger menu for small screens" do
    get root_path
    expect(response.body).to include('aria-label="Menu"', "More ▾", "New project")
    expect(response.body).to include('aria-current="page"')
  end
end
