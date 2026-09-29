class AddSjaaLink < ActiveRecord::Migration[8.1]
  def change
    # A member who logs in with SJAA is linked to their Person in the SJAA
    # membership database; the membership state is a copy, refreshed on each
    # SJAA login or from the profile page.
    add_column :users, :sjaa_person_id, :bigint
    add_column :users, :sjaa_linked_at, :datetime
    add_column :users, :sjaa_membership_active, :boolean, default: false, null: false
    add_column :users, :sjaa_membership_expires_on, :date
    add_column :users, :sjaa_membership_checked_at, :datetime
    add_index :users, :sjaa_person_id, unique: true

    add_column :telescopes, :requires_sjaa_membership, :boolean, default: false, null: false
  end
end
