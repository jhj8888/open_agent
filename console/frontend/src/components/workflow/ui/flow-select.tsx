import React from 'react';
import { Select, type SelectProps } from 'antd';
import type { DefaultOptionType } from 'antd/es/select';
import { cn } from '@/utils';
import { useTranslation } from 'react-i18next';

import formSelect from '@/assets/imgs/main/icon_nav_dropdown.svg';

function FLowSelect<
  ValueType = string,
  OptionType extends DefaultOptionType = DefaultOptionType,
>(props: SelectProps<ValueType, OptionType>): React.ReactElement {
  const { className = '', children, ...reset } = props;

  const { t } = useTranslation();

  return (
    <Select<ValueType, OptionType>
      suffixIcon={<img src={formSelect} className="w-4 h-4" />}
      placeholder={t('common.pleaseSelect')}
      className={cn('flow-select nodrag w-full', className)}
      dropdownAlign={{ offset: [0, 0] }}
      dropdownRender={menu => (
        <div
          onWheel={e => {
            e.stopPropagation();
          }}
        >
          {menu}
        </div>
      )}
      {...reset}
    >
      {children}
    </Select>
  );
}

FLowSelect.Option = Select.Option;

export default FLowSelect;
