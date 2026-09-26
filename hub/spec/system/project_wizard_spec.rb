require "rails_helper"

RSpec.describe "Project wizard", type: :system do
  let(:user) { create(:user) }
  let(:telescope) { create(:telescope, name: "Backyard 16in") }
  let!(:train) { create(:optical_train, telescope: telescope, key: "esprit100_2600mm") }
  let!(:m31) do
    create(:astro_object, primary_name: "Andromeda Galaxy", ra_deg: 10.68479, dec_deg: 41.26906).tap { |o| o.add_alias("M 31", catalog: "Messier") }
  end

  before { sign_in user }

  it "creates a project from a catalogue search, a telescope and an exposure plan" do
    visit new_project_path
    fill_in "q", with: "m31"
    click_on "Search"
    expect(page).to have_text("Andromeda Galaxy")
    click_on "Add", match: :first
    expect(page).to have_button("Remove")

    click_on "Continue"
    expect(page).to have_text("Which telescope would you like to use?")
    find("input[type=radio][value='#{train.id}']").click
    click_on "Continue"

    expect(page).to have_text("Plan your exposures")
    select "Ha", from: "filter"
    fill_in "exposure_seconds", with: "300"
    fill_in "desired_count", with: "20"
    click_on "Add"
    expect(page).to have_button("Remove")
    click_on "Continue"

    expect(page).to have_text("Review")
    fill_in "name", with: "Andromeda deep"
    click_on "Submit project"

    expect(page).to have_text("Andromeda deep")
    project = Project.find_by!(name: "Andromeda deep")
    expect(page).to have_current_path(project_path(project))
    expect(project.targets.sole.exposure_plans.sole).to have_attributes(filter: "Ha", exposure_seconds: 300, desired_count: 20)
  end
end
