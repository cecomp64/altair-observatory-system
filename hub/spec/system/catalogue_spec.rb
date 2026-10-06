require "rails_helper"

RSpec.describe "Catalogue", type: :system do
  let(:user) { create(:user) }
  let!(:telescope) { create(:telescope) }
  let!(:m31) do
    create(:astro_object, primary_name: "Andromeda Galaxy", ra_deg: 10.68479, dec_deg: 41.26906, object_type: "Galaxy", constellation: "And")
  end

  before { sign_in user }

  it "highlights a row on hover and opens the object from anywhere in it" do
    visit objects_path(telescope: telescope.slug)
    row = find("tr[data-object-row='#{m31.id}']")
    background = -> { page.evaluate_script("getComputedStyle(document.querySelector(\"tr[data-object-row='#{m31.id}']\")).backgroundColor") }
    before_hover = background.call
    row.hover
    expect(background.call).not_to eq(before_hover)

    # Click the row away from the name, on its constellation cell (which the
    # name's link covers).
    cell = row.find("td", text: "And", exact_text: true)
    offset = page.evaluate_script("arguments[0].getBoundingClientRect().left - arguments[1].getBoundingClientRect().left", cell, row)
    row.click(x: offset + 5, y: 10)
    expect(page).to have_current_path(object_path(m31, telescope: telescope.slug))
  end
end
