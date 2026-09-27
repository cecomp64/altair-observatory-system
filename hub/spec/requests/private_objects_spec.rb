require "rails_helper"

RSpec.describe "Members' custom catalogue objects", type: :request do
  let(:owner) { create(:user) }
  let(:member) { create(:user) }
  let(:admin) { create(:user, :admin) }
  let!(:mine) { create(:astro_object, :custom, created_by: owner, primary_name: "Backyard Blob") }
  let!(:catalogue) { create(:astro_object, primary_name: "Andromeda Galaxy") }

  it "creates custom objects private by default, visible only to the creator and admins" do
    sign_in owner
    post objects_path, params: { astro_object: { primary_name: "Garden Nebula", ra: "05h 35m 17s", dec: "−05° 23′ 28″" } }
    created = AstroObject.find_by!(primary_name: "Garden Nebula")
    expect(created).to have_attributes(source: "custom", shared: false, created_by: owner)
    expect(created.ra_deg.to_f).to be_within(0.001).of(83.8208)

    sign_in member
    get objects_path(q: "Garden Nebula", telescope: "none")
    expect(response.body).not_to include(object_path(created))
    get object_path(created)
    expect(response).to redirect_to(root_path)

    sign_in admin
    get object_path(created)
    expect(response).to have_http_status(:ok)
  end

  it "hides others' private objects from the catalogue, the wizard and name resolution" do
    sign_in member
    get objects_path(telescope: "none")
    expect(response.body).to include("Andromeda Galaxy")
    expect(response.body).not_to include("Backyard Blob")

    get new_project_path(q: "Backyard Blob")
    expect(response.body).not_to include(%(value="#{mine.id}"))
    post project_wizard_add_object_path, params: { astro_object_id: mine.id }
    expect(flash[:alert]).to be_present

    object, outcome = Catalogue::NameResolver.new(client: nil).resolve("Backyard Blob", created_by: member)
    expect(object).to be_nil
    expect(outcome).not_to eq(:local)
    expect(Catalogue::NameResolver.new.resolve("Backyard Blob", created_by: owner)).to eq([ mine, :local ])
  end

  it "lets the creator share it with every member and make it private again" do
    sign_in member
    patch share_object_path(mine), params: { shared: "1" }
    expect(mine.reload).not_to be_shared

    sign_in owner
    get object_path(mine)
    expect(response.body).to include("Custom · private", "Share with members")
    patch share_object_path(mine), params: { shared: "1" }
    expect(mine.reload).to be_shared

    sign_in member
    get object_path(mine)
    expect(response).to have_http_status(:ok)
    expect(response.body).to include("added by #{owner.display_name}")

    sign_in owner
    patch share_object_path(mine), params: { shared: "0" }
    expect(mine.reload).not_to be_shared
  end
end
