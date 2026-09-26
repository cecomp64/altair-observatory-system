require "rails_helper"

RSpec.describe "Master downloads", type: :request do
  let(:owner) { create(:user) }
  let(:project) { create(:project, user: owner) }
  let(:target) { create(:target, project: project, user: owner) }
  let(:master) { create(:data_product, target: target, kind: :multi_night_master, archive_uri: "s3://astro-archive/altair/projects/T1/multinight/Ha_v1.xisf") }

  around do |example|
    env = { "ARCHIVE_READER_ACCESS_KEY_ID" => "AKIA_TEST", "ARCHIVE_READER_SECRET_ACCESS_KEY" => "secret",
            "ARCHIVE_READER_REGION" => "us-west-2", "ARCHIVE_BUCKET" => "astro-archive" }
    old = env.keys.index_with { |k| ENV[k] }
    env.each { |k, v| ENV[k] = v }
    example.run
  ensure
    old.each { |k, v| ENV[k] = v }
  end

  it "redirects the owner to a presigned archive link, and the target page offers it" do
    sign_in owner

    get target_path(target.tap { master })
    expect(response.body).to include(download_data_product_path(master))

    get download_data_product_path(master)
    expect(response).to have_http_status(:redirect)
    expect(response.location).to start_with("https://astro-archive.s3.us-west-2.amazonaws.com/altair/projects/T1/multinight/Ha_v1.xisf?")
  end

  it "offers a target's masters zip and a night's calibrated-subs zip as presigned links" do
    zip = create(:data_product, target: target, kind: :masters_bundle, filter: "all", version: 2, size_bytes: 905_000_000,
                                archive_uri: "s3://astro-archive/altair/projects/T1/bundles/masters.zip",
                                metrics: { "contents" => [ { "filter" => "Ha", "version" => 3, "name" => "T1_Ha_v003.xisf" } ] })
    night = create(:data_product, target: target, kind: :night_master, filter: "Ha", night: Date.new(2026, 9, 24),
                                  archive_uri: "s3://astro-archive/altair/projects/T1/nights/2026-09-24/Ha/night_master_1.xisf",
                                  calibrated_bundle: { "sha256" => "a" * 64, "size_bytes" => 9_663_676_416, "frames" => 96,
                                                       "archive_uri" => "s3://astro-archive/altair/projects/T1/nights/2026-09-24/Ha/calibrated_1.zip" })
    sign_in owner

    get target_path(target)
    expect(response.body).to include("All masters", "Ha v3", "863 MB zip", download_data_product_path(zip))
    expect(response.body).to include("Calibrated subs (96, 9 GB zip)", download_data_product_path(night, part: "calibrated"))

    get download_data_product_path(zip)
    expect(response.location).to start_with("https://astro-archive.s3.us-west-2.amazonaws.com/altair/projects/T1/bundles/masters.zip?")
    get download_data_product_path(night, part: "calibrated")
    expect(response.location).to start_with("https://astro-archive.s3.us-west-2.amazonaws.com/altair/projects/T1/nights/2026-09-24/Ha/calibrated_1.zip?")
    expect(CGI.unescape(response.location)).to include('filename="calibrated_1.zip"')

    master   # the project page lists the zip with the latest multi-night masters
    get project_path(project)
    expect(response.body).to include(download_data_product_path(zip))
  end

  it "has no calibrated link before the zip is archived" do
    night = create(:data_product, target: target, kind: :night_master, filter: "Ha", night: Date.new(2026, 9, 24),
                                  calibrated_bundle: { "sha256" => "a" * 64, "size_bytes" => 1, "frames" => 3, "archive_uri" => nil })
    sign_in owner
    get target_path(target)
    expect(response.body).not_to include(download_data_product_path(night, part: "calibrated"))
    get download_data_product_path(night, part: "calibrated")
    expect(response.location).not_to include("amazonaws")
  end

  it "refuses members who can't see the project" do
    sign_in create(:user)
    get download_data_product_path(master)
    expect(response.location).not_to include("amazonaws")
  end

  it "doesn't offer a download when the archive reader isn't configured" do
    ENV["ARCHIVE_BUCKET"] = nil
    sign_in owner
    get target_path(target.tap { master })
    expect(response.body).not_to include(download_data_product_path(master))
  end

  describe "reports" do
    let(:markdown) do
      <<~MD
        # T1 · Ha · night 2026-09-24

        Final night master on **esprit**. <script>alert(1)</script>

        | Frame | Result | FWHM |
        | :--- | :--- | ---: |
        | light_1.fits | used | 2.5 |
        | light_2.fits | rejected: trail | 3.1 |

        - [issue](https://hub.example/issues/1) and [bad](javascript:alert(1))

        ![coverage](coverage.jpg)

        <img src=x onerror=alert(1)>
      MD
    end

    before { master.report.attach(io: StringIO.new(markdown), filename: "r.md", content_type: "text/markdown") }

    it "renders the report as HTML with its tables, and nothing executable" do
      sign_in owner
      get target_path(target)
      expect(response.body).to include(report_data_product_path(master))

      get report_data_product_path(master)
      expect(response).to have_http_status(:ok)
      page = Nokogiri::HTML(response.body).at_css("article.altair-report")
      expect(page.at_css("h1").text).to eq("T1 · Ha · night 2026-09-24")
      expect(page.at_css("strong").text).to eq("esprit")
      expect(page.css("table tbody tr").map { |tr| tr.css("td").map(&:text) }).to eq([ %w[light_1.fits used 2.5], [ "light_2.fits", "rejected: trail", "3.1" ] ])
      expect(page.css("th").last["style"]).to eq("text-align: right")
      expect(page.at_css("a[href='https://hub.example/issues/1']")["rel"]).to eq("noopener nofollow")
      # Raw HTML is shown as text, never as elements; no scripts, images or javascript: links survive.
      expect(page.css("script, img, style, [onerror]")).to be_empty
      expect(page.css("a").map { |a| a["href"] }.compact).to eq([ "https://hub.example/issues/1" ])
      expect(page.text).to include("<img src=x onerror=alert(1)>")
    end

    it "is only for people who can see the project" do
      sign_in create(:user)
      get report_data_product_path(master)
      expect(response).not_to have_http_status(:ok)
    end
  end
end
