require "rails_helper"

RSpec.describe "Telescopes that require an SJAA membership", type: :request do
  let(:telescope) { create(:telescope, name: "Members Scope", requires_sjaa_membership: true) }
  let!(:train) { create(:optical_train, telescope: telescope, key: "members_train") }
  let(:user) { create(:user) }

  def link(user, ends_on:)
    user.link_sjaa!(Sjaa::Person.new(id: user.id + 1000, first_name: "Ada", last_name: "L", email: user.email,
                                     emails: [ user.email ], membership_ends: [ ends_on ], lifetime: false))
  end

  it "only offers the option when SJAA is set up, or to clear it" do
    sign_in create(:user, :admin)
    get new_admin_telescope_path
    expect(response.body).not_to include("Requires an active SJAA membership")

    get edit_admin_telescope_path(telescope)
    expect(response.body).to include("Requires an active SJAA membership", "SJAA isn't set up on this Hub")
  end

  it "lets an admin require a membership when creating a telescope" do
    allow(Sjaa::Client).to receive(:configured?).and_return(true)
    sign_in create(:user, :admin)
    get new_admin_telescope_path
    expect(response.body).to include("Requires an active SJAA membership")

    post admin_telescopes_path, params: { telescope: { name: "Club Scope", latitude: "37.3", longitude: "-121.9",
                                                       timezone: "America/Los_Angeles", min_altitude_deg: 30,
                                                       requires_sjaa_membership: "1" } }
    expect(Telescope.find_by!(name: "Club Scope")).to be_requires_sjaa_membership
  end

  context "in the project wizard" do
    before { sign_in user }

    it "shows the telescope but won't let an unlinked member choose it" do
      get project_wizard_telescope_path
      expect(response.body).to include("Members Scope", "SJAA members", "Link your SJAA membership")
      expect(Nokogiri::HTML(response.body).at_css("input[name=optical_train_id][value='#{train.id}']")["disabled"]).to be_present

      patch new_project_path, params: { optical_train_id: train.id }
      expect(response).to have_http_status(:unprocessable_content)
      expect(response.body).to include("needs a current SJAA membership")
    end

    it "refuses a linked member whose membership has lapsed" do
      link(user, ends_on: Date.current - 1)
      patch new_project_path, params: { optical_train_id: train.id }
      expect(response).to have_http_status(:unprocessable_content)
      expect(response.body).to include("Renew it, then refresh")
    end

    it "lets a current member choose it, and stops them if the membership lapses mid-way" do
      link(user, ends_on: Date.current + 1)
      patch new_project_path, params: { optical_train_id: train.id }
      expect(response).to redirect_to(project_wizard_objects_path)

      travel 3.days do
        get project_wizard_objects_path
        expect(response).to redirect_to(project_wizard_telescope_path)
        expect(flash[:alert]).to include("needs a current SJAA membership")
      end
    end

    it "doesn't restrict admins or other telescopes" do
      open_scope = create(:telescope)
      open_train = create(:optical_train, telescope: open_scope, key: "open_train")
      patch new_project_path, params: { optical_train_id: open_train.id }
      expect(response).to redirect_to(project_wizard_objects_path)

      sign_in create(:user, :admin)
      patch new_project_path, params: { optical_train_id: train.id }
      expect(response).to redirect_to(project_wizard_objects_path)
    end
  end

  it "won't resume or reopen work on the telescope without a current membership" do
    sign_in user
    target = create(:target, user: user, telescope: telescope, optical_train: train, paused_at: Time.current)
    post resume_target_path(target)
    expect(flash[:alert]).to include("needs a current SJAA membership")
    expect(target.reload).to be_paused

    target.project.update!(status: :paused)
    post resume_project_path(target.project)
    expect(flash[:alert]).to include("Members Scope needs a current SJAA membership")
    expect(target.project.reload).to be_paused

    link(user, ends_on: Date.current + 30)
    post resume_target_path(target)
    expect(target.reload).not_to be_paused
    post resume_project_path(target.project)
    expect(target.project.reload).to be_active
  end
end
