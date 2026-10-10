class PriorityLevels < ActiveRecord::Migration[8.1]
  # Priorities become Target Scheduler's three levels (0 low, 1 normal, 2 high)
  # instead of a free-form integer: below zero was low, zero (the old default)
  # normal, above zero high.
  def up
    %i[projects targets].each do |table|
      execute "UPDATE #{table} SET priority = CASE WHEN priority < 0 THEN 0 WHEN priority = 0 THEN 1 ELSE 2 END"
      change_column_default table, :priority, from: 0, to: 1
    end
  end

  def down
    raise ActiveRecord::IrreversibleMigration, "the original integer priorities are not kept"
  end
end
