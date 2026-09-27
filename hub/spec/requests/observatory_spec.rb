require "rails_helper"

RSpec.describe "Observatory status", type: :request do
  include ActiveSupport::Testing::TimeHelpers

  let(:member) { create(:user) }
  let(:owner) { create(:user) }
  let(:telescope) { create(:telescope, name: "Backyard 16in", latitude: 37.3, longitude: -121.9, timezone: "America/Los_Angeles") }
  let!(:train) { create(:optical_train, telescope: telescope) }
  let(:shared) { create(:project, user: owner, visibility: "club") }
  let(:private_project) { create(:project, user: owner) }
  let!(:target) { create(:target, project: shared, user: owner, telescope: telescope, optical_train: train, name: "Pelican") }
  let!(:hidden) { create(:target, project: private_project, user: owner, telescope: telescope, optical_train: train, name: "Secret") }
  let(:now) { Time.utc(2026, 9, 25, 6, 30) } # 23:30 local on the night of Sept 24

  def light(target, at, night: "2026-09-24")
    Frame.create!(sha256: SecureRandom.hex(32), telescope: telescope, optical_train: train, target: target, project: target.project,
                  image_type: "light", night: night, date_obs: at, filter: "Ha", exposure_s: 300,
                  file_name: "#{SecureRandom.hex(4)}.fits", logical_path: "raw/x.fits")
  end

  before do
    sign_in member
    travel_to now
  end

  after { travel_back }

  it "shows a telescope imaging now, with tonight's frames and last nights" do
    ObservingNight.create!(telescope: telescope, optical_train: train, night: "2026-09-24", roof_open_at: Time.utc(2026, 9, 25, 3))
    ObservingNight.create!(telescope: telescope, optical_train: train, night: "2026-09-22",
                           roof_open_at: Time.utc(2026, 9, 23, 3), roof_closed_at: Time.utc(2026, 9, 23, 9))
    2.times { |i| light(hidden, Time.utc(2026, 9, 25, 5, i)) }
    light(target, Time.utc(2026, 9, 25, 6, 20))
    light(target, Time.utc(2026, 9, 23, 5), night: "2026-09-22")

    get observatory_path

    expect(response).to have_http_status(:ok)
    body = response.body
    expect(body).to include("Backyard 16in", "Imaging", "Roof open since 20:00", "Pelican", "Last frame 23:20")
    expect(body).to include("A member&#39;s target").or include("A member's target")
    expect(body).not_to include("Secret")
    expect(body).to include("3</span> lights", "20:00–02:00", "1 lights · 0.08 h")
    expect(body).to include("2 targets from 1 member")
  end

  it "shows closed when it's dark and the roof never opened, and maintenance when an admin says so" do
    get observatory_path
    expect(response.body).to include("Closed", "roof hasn&#39;t opened tonight")

    telescope.update!(operating_status: "maintenance", status_note: "Mirror cleaning until Friday")
    get observatory_path
    expect(response.body).to include("Maintenance", "Mirror cleaning until Friday")
  end

  it "waits for dark in the evening and reports a finished night" do
    travel_to Time.utc(2026, 9, 25, 1) # 18:00 local
    get observatory_path
    expect(response.body).to include("Waiting for dark", "Astronomical darkness from")

    travel_to Time.utc(2026, 9, 25, 12) # 05:00 local
    ObservingNight.create!(telescope: telescope, optical_train: train, night: "2026-09-24",
                           roof_open_at: Time.utc(2026, 9, 25, 3), roof_closed_at: Time.utc(2026, 9, 25, 11))
    get observatory_path
    expect(response.body).to include("Done for the night", "Opened 20:00, closed 04:00")
  end

  it "resumes imaging when the roof opens again after a close" do
    key = create(:api_key, telescope: telescope)
    headers = { "Authorization" => "Bearer #{key.plaintext_token}" }
    post "/api/v1/telescopes/#{telescope.slug}/sessions", params: { event: "roof_open", at: "2026-09-25T03:00:00Z" }, headers: headers
    post "/api/v1/telescopes/#{telescope.slug}/sessions", params: { event: "roof_close", at: "2026-09-25T04:00:00Z" }, headers: headers
    post "/api/v1/telescopes/#{telescope.slug}/sessions", params: { event: "roof_open", at: "2026-09-25T05:00:00Z" }, headers: headers

    get observatory_path
    expect(response.body).to include("Imaging", "Roof open since 22:00")
  end

  it "refreshes open pages when the roof opens" do
    expect {
      ObservingNight.create!(telescope: telescope, optical_train: train, night: "2026-09-24", roof_open_at: Time.current)
    }.to have_enqueued_job(Turbo::Streams::BroadcastStreamJob)
  end
end
