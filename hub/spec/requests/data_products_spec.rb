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
end
