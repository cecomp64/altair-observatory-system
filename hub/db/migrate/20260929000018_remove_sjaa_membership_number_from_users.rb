class RemoveSjaaMembershipNumberFromUsers < ActiveRecord::Migration[8.1]
  # Superseded by the SJAA link (sjaa_person_id and the membership columns);
  # the self-entered number was never checked against SJAA.
  def change
    remove_column :users, :sjaa_membership_number, :string
  end
end
