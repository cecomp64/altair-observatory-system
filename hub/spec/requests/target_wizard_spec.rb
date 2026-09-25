require "rails_helper"

RSpec.describe "TargetWizard", type: :request do
  let(:user) { create(:user) }
  let(:telescope) { create(:telescope) }

  before { sign_in user }

  it "walks telescope -> details -> exposures -> review -> create and persists a submitted target" do
    patch new_target_path, params: { telescope_id: telescope.id }
    expect(response).to redirect_to(target_wizard_details_path)

    patch target_wizard_details_path, params: { name: "M31", ra: "00:42:44", dec: "+41:16:09", notes: "Andromeda" }
    expect(response).to redirect_to(target_wizard_exposures_path)

    post target_wizard_add_exposure_plan_path, params: { filter: "Luminance", exposure_seconds: 300, desired_count: 20 }
    expect(response).to redirect_to(target_wizard_exposures_path)

    patch target_wizard_exposures_path
    expect(response).to redirect_to(target_wizard_review_path)

    expect {
      post target_wizard_create_path
    }.to change(Target, :count).by(1)

    target = Target.order(:created_at).last
    expect(response).to redirect_to(target_path(target))
    expect(target.user).to eq(user)
    expect(target.telescope).to eq(telescope)
    expect(target.name).to eq("M31")
    expect(target).to be_submitted
    expect(target.exposure_plans.sole).to have_attributes(filter: "Luminance", exposure_seconds: 300, desired_count: 20)
  end

  it "refuses to continue past details without a chosen telescope" do
    get target_wizard_details_path
    expect(response).to redirect_to(new_target_path)
  end

  it "rejects an invalid coordinate and re-renders the details step" do
    patch new_target_path, params: { telescope_id: telescope.id }

    patch target_wizard_details_path, params: { name: "Bad", ra: "not a number", dec: "0" }

    expect(response).to have_http_status(:unprocessable_content)
  end
end
