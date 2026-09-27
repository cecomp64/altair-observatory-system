require "rails_helper"
require Rails.root.join("db/migrate/20260927000015_share_admin_imported_objects")

RSpec.describe ShareAdminImportedObjects do
  it "shares admins' imported custom objects and ownerless ones, and nothing else" do
    admin = create(:user, :admin)
    member = create(:user)
    admin_import = create(:astro_object, :custom, created_by: admin, source_ref: "astrodb:1")
    member_import = create(:astro_object, :custom, created_by: member, source_ref: "astrodb:2")
    admin_made = create(:astro_object, :custom, created_by: admin)
    ownerless = create(:astro_object, :custom, created_by: nil)

    ActiveRecord::Migration.suppress_messages { described_class.new.up }

    expect([ admin_import, member_import, admin_made, ownerless ].map { |o| o.reload.shared? }).to eq([ true, false, false, true ])
  end
end
